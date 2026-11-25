"""Alert rule evaluation engine (Phase 3 feature 4).

An *alert* is a configured RULE (``is_active``) that, when evaluated against
actual persisted data, may become *triggered* (``is_triggered`` +
``last_triggered_at`` + ``trigger_message``). This service owns rule CRUD and
the deterministic, explicit evaluation of each rule type. It does NOT deliver
notifications — that is a separate, best-effort step (see
:mod:`slbm_tracker.services.alert_delivery_service`) so a delivery failure can
never destroy a trigger.

Design rules (Phase 3 spec §4):

* **Deterministic + explicit.** Every rule type has one written condition with a
  deliberate comparison operator. ``>`` and ``>=`` are NOT interchangeable and
  are chosen per type (documented on each ``_eval_*`` helper). Comparisons run
  through :func:`to_decimal` so a value exactly at the threshold is decided
  exactly, without binary-float surprise.
* **Edge-triggered dedup.** A rule fires (updates its timestamp/message) only on
  the transition from not-triggered to condition-met. While the condition keeps
  holding, re-evaluation does not re-fire (dedup) and the timestamp stays put,
  so ``last_triggered_at`` is meaningful. When the condition stops holding the
  rule re-arms (``is_triggered`` back to ``False``); the historical
  ``last_triggered_at`` is preserved.
* **Never fabricate data.** Market-data rules read only persisted
  :class:`SLBSnapshot` rows; contract rules read real contracts. If the data
  needed to evaluate a rule is absent, the rule simply does not fire (it is
  never a false positive).
* **Configurable thresholds.** Each rule's ``threshold`` overrides the matching
  config default; if unset, the config default is used.

Timezone: contract *date* logic (expiry reminders) uses today-in-IST via
:func:`today_ist`. Event timestamps (``last_triggered_at``) are stored naive-UTC
to match every other timestamp in the app (snapshots, records), which the
frontend formatter already renders as UTC.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from slbm_tracker.config import get_settings
from slbm_tracker.db.models import Alert, SLBSnapshot, Stock
from slbm_tracker.services import business_config
from slbm_tracker.services.contract_service import ContractService, today_ist
from slbm_tracker.utils.money import to_decimal

logger = logging.getLogger(__name__)

# Supported alert rule types (mirrors the Alert.alert_type column comment).
ALERT_YIELD_THRESHOLD = "YIELD_THRESHOLD"
ALERT_ABOVE_AVERAGE = "ABOVE_AVERAGE"
ALERT_TIGHT_SPREAD = "TIGHT_SPREAD"
ALERT_ROLLOVER = "ROLLOVER"
ALERT_EXPIRY_REMINDER = "EXPIRY_REMINDER"
# Phase 3.6: an actionable Series B opportunity (net of charges, at the bid).
ALERT_LENDING_OPPORTUNITY = "LENDING_OPPORTUNITY"

ALERT_TYPES = frozenset(
    {
        ALERT_YIELD_THRESHOLD,
        ALERT_ABOVE_AVERAGE,
        ALERT_TIGHT_SPREAD,
        ALERT_ROLLOVER,
        ALERT_EXPIRY_REMINDER,
        ALERT_LENDING_OPPORTUNITY,
    }
)

# Minimum snapshots required before an ABOVE_AVERAGE rule may fire; with a
# single data point the "average" equals the latest value and firing would be
# meaningless. Guards against thin-data false positives.
_MIN_SAMPLES_FOR_AVERAGE = 2


@dataclass(frozen=True)
class AlertEvaluation:
    """Outcome of evaluating one alert rule in a single run."""

    alert_id: int
    alert_type: str
    stock_id: Optional[int]
    threshold_used: float
    condition_met: bool
    fired_now: bool  # True only on the transition into triggered (edge) — dedup
    message: Optional[str]
    # Phase 5.4: the rule re-crossed inside its cooldown, so nothing new was sent.
    cooldown_suppressed: bool = False


class AlertService:
    """Create/read alert rules and evaluate them against persisted data."""

    # -- threshold defaults per type (resolved from config at call time) -----

    @staticmethod
    def default_threshold(alert_type: str) -> float:
        return {
            ALERT_YIELD_THRESHOLD: business_config.effective("default_alert_yield_threshold"),
            ALERT_TIGHT_SPREAD: business_config.effective("tight_spread_threshold_pct"),
            ALERT_ABOVE_AVERAGE: business_config.effective("above_average_yield_margin_pct"),
            ALERT_EXPIRY_REMINDER: float(business_config.effective("expiring_soon_days")),
            ALERT_ROLLOVER: float(business_config.effective("rollover_warn_days")),
            ALERT_LENDING_OPPORTUNITY: business_config.effective("min_net_yield_pct"),
        }.get(alert_type, 0.0)

    @classmethod
    def _threshold_for(cls, alert: Alert) -> float:
        if alert.threshold is not None:
            return float(alert.threshold)
        return cls.default_threshold(alert.alert_type)

    # -- rule CRUD -----------------------------------------------------------

    @classmethod
    def create_alert(
        cls,
        db: Session,
        *,
        alert_type: str,
        threshold: Optional[float] = None,
        stock_id: Optional[int] = None,
        is_active: bool = True,
    ) -> Alert:
        """Create an alert rule. Validates the type and any referenced stock."""
        if alert_type not in ALERT_TYPES:
            raise ValueError(
                f"Unknown alert_type '{alert_type}'. Valid: {sorted(ALERT_TYPES)}."
            )
        if stock_id is not None:
            stock = db.query(Stock).filter(Stock.id == stock_id).first()
            if stock is None:
                raise ValueError(f"Stock id {stock_id} not found.")
        alert = Alert(
            alert_type=alert_type,
            threshold=threshold,
            stock_id=stock_id,
            is_active=is_active,
            is_triggered=False,
        )
        db.add(alert)
        db.commit()
        db.refresh(alert)
        return alert

    @staticmethod
    def list_alerts(db: Session, *, active_only: bool = False) -> list[Alert]:
        q = db.query(Alert)
        if active_only:
            q = q.filter(Alert.is_active.is_(True))
        return q.order_by(Alert.created_at.desc()).all()

    @staticmethod
    def set_active(db: Session, alert_id: int, is_active: bool) -> Optional[Alert]:
        alert = db.query(Alert).filter(Alert.id == alert_id).first()
        if alert is None:
            return None
        alert.is_active = is_active
        db.commit()
        db.refresh(alert)
        return alert

    @staticmethod
    def delete_alert(db: Session, alert_id: int) -> bool:
        alert = db.query(Alert).filter(Alert.id == alert_id).first()
        if alert is None:
            return False
        db.delete(alert)
        db.commit()
        return True

    # -- snapshot helpers (persisted data only) ------------------------------

    @staticmethod
    def _latest_snapshots(
        db: Session, symbol: Optional[str] = None, *, today: datetime.date
    ) -> list[SLBSnapshot]:
        """Most recent Series B snapshot per (symbol, family, expiry) for unexpired series.

        Optionally one symbol. Only the configured lending family (``series_family``,
        Series B) is read: Series A, special series and legacy rows without a
        family never reach alerts. A series whose expiry is before ``today`` (IST)
        has settled and is excluded; one expiring today still counts.

        Selection is fully deterministic even when several snapshots for one
        (symbol, expiry_date) share the max timestamp — which the Phase 4
        scheduler makes routine, since it writes every series at a single
        ``utcnow()`` per tick. We first take the max timestamp per group, then
        the max ``id`` among the rows at that timestamp, so exactly ONE row is
        returned per group (the newest, tie-broken by insertion order) instead
        of a nondeterministic set.
        """
        family = get_settings().series_family
        max_ts_q = db.query(
            SLBSnapshot.symbol.label("symbol"),
            SLBSnapshot.series_family.label("series_family"),
            SLBSnapshot.expiry_date.label("expiry_date"),
            func.max(SLBSnapshot.timestamp).label("max_ts"),
        ).filter(SLBSnapshot.expiry_date >= today, SLBSnapshot.series_family == family)
        if symbol is not None:
            max_ts_q = max_ts_q.filter(SLBSnapshot.symbol == symbol)
        max_ts_subq = max_ts_q.group_by(
            SLBSnapshot.symbol, SLBSnapshot.series_family, SLBSnapshot.expiry_date
        ).subquery()

        # Among the rows tied at each group's max timestamp, keep the max id.
        # id is the globally-unique PK, so the final row lookup needs no further
        # (symbol, expiry) predicate.
        max_id_subq = (
            db.query(func.max(SLBSnapshot.id).label("max_id"))
            .join(
                max_ts_subq,
                (SLBSnapshot.symbol == max_ts_subq.c.symbol)
                & (SLBSnapshot.series_family == max_ts_subq.c.series_family)
                & (SLBSnapshot.expiry_date == max_ts_subq.c.expiry_date)
                & (SLBSnapshot.timestamp == max_ts_subq.c.max_ts),
            )
            .group_by(SLBSnapshot.symbol, SLBSnapshot.series_family, SLBSnapshot.expiry_date)
            .subquery()
        )

        return (
            db.query(SLBSnapshot)
            .join(max_id_subq, SLBSnapshot.id == max_id_subq.c.max_id)
            .all()
        )

    @classmethod
    def _symbol_for(cls, db: Session, alert: Alert) -> Optional[str]:
        """Resolve the alert's scoped symbol (None => all symbols)."""
        if alert.stock_id is None:
            return None
        stock = db.query(Stock).filter(Stock.id == alert.stock_id).first()
        return stock.symbol if stock else None

    # -- per-type evaluation (explicit operators) ----------------------------

    @classmethod
    def _eval_yield_threshold(
        cls, db: Session, alert: Alert, threshold: float, today: datetime.date
    ) -> tuple[bool, Optional[str]]:
        """Fire when a snapshot's annualized yield is AT OR ABOVE the threshold.

        Operator: ``>=`` (a yield exactly equal to the threshold DOES fire).
        """
        symbol = cls._symbol_for(db, alert)
        if alert.stock_id is not None and symbol is None:
            return False, None
        snaps = [
            s for s in cls._latest_snapshots(db, symbol, today=today) if s.annualized_yield is not None
        ]
        thr = to_decimal(threshold)
        hits = [s for s in snaps if to_decimal(s.annualized_yield) >= thr]
        if not hits:
            return False, None
        best = max(hits, key=lambda s: s.annualized_yield or 0.0)
        return True, (
            f"{best.symbol} {best.series or '—'} gross yield "
            f"{best.annualized_yield:.2f}% ≥ threshold {threshold:.2f}%."
        )

    @classmethod
    def _eval_tight_spread(
        cls, db: Session, alert: Alert, threshold: float, today: datetime.date
    ) -> tuple[bool, Optional[str]]:
        """Fire when the bid/ask spread is AT OR BELOW the threshold percent.

        Operator: ``<=`` (a spread exactly equal to the threshold DOES fire).
        Spread%% is DERIVED from persisted best_bid/best_ask —
        ``(best_ask - best_bid) / best_ask * 100`` — because the snapshot does
        not persist NSE's own spread%%. Skipped when best_ask is missing/zero.
        """
        symbol = cls._symbol_for(db, alert)
        if alert.stock_id is not None and symbol is None:
            return False, None
        thr = to_decimal(threshold)
        best_hit: Optional[tuple] = None
        for s in cls._latest_snapshots(db, symbol, today=today):
            if s.best_ask is None or s.best_bid is None or s.best_ask <= 0:
                continue
            ask = to_decimal(s.best_ask)
            bid = to_decimal(s.best_bid)
            spread_pct = (ask - bid) / ask * to_decimal(100)
            if spread_pct <= thr:
                pct = float(spread_pct)
                if best_hit is None or pct < best_hit[0]:
                    best_hit = (pct, s)
        if best_hit is None:
            return False, None
        pct, s = best_hit
        return True, (
            f"{s.symbol} {s.series or '—'} spread {pct:.2f}% ≤ threshold {threshold:.2f}% "
            f"(bid ₹{s.best_bid:.4g} / ask ₹{s.best_ask:.4g})."
        )

    @classmethod
    def _eval_above_average(
        cls, db: Session, alert: Alert, threshold_margin_pct: float
    ) -> tuple[bool, Optional[str]]:
        """Fire when the latest yield exceeds the TRAILING average by the margin.

        Operator: ``>=`` on ``latest_yield`` vs ``avg_yield * (1 + margin/100)``.

        The average is computed over the most recent
        ``above_average_lookback_samples`` snapshots for the symbol (a bounded,
        genuinely *trailing* window), NOT over all persisted history. The window
        is essential: Phase 4 auto-ingests a snapshot every scheduler tick, so an
        all-history mean would only ever creep toward a long-run value and the
        rule would fire progressively less over time — the opposite of surfacing
        a current above-average opportunity. Bounding to a trailing window lets
        old readings age out so a genuine recent spike still clears the bar.

        Needs at least two snapshots for the symbol (else the "average" is just
        the latest value); scoped rules require a specific symbol. Never
        fabricates — absent data means no fire.
        """
        symbol = cls._symbol_for(db, alert)
        if symbol is None:
            # ABOVE_AVERAGE compares a symbol against its own history; it needs a
            # scoped stock. An unscoped rule cannot be evaluated meaningfully.
            return False, None

        # Bound the trailing window. ``ge=2`` on the setting already floors it at
        # _MIN_SAMPLES_FOR_AVERAGE; max() is a defensive guard if the singleton is
        # mutated below that in a test. Ordering desc + limit fetches the most
        # recent N rows only, so history beyond the window never dilutes the mean.
        lookback = max(_MIN_SAMPLES_FOR_AVERAGE, get_settings().above_average_lookback_samples)

        # Pick the reference contract deterministically: the most recent snapshot
        # for this symbol that carries a yield. ``timestamp.desc()`` then
        # ``id.desc()`` so tied timestamps (the scheduler writes every series at a
        # single utcnow() per tick) resolve to the highest id — a stable "latest".
        family = get_settings().series_family
        latest_snap = (
            db.query(SLBSnapshot)
            .filter(
                SLBSnapshot.symbol == symbol,
                SLBSnapshot.series_family == family,
                SLBSnapshot.annualized_yield.isnot(None),
            )
            .order_by(SLBSnapshot.timestamp.desc(), SLBSnapshot.id.desc())
            .first()
        )
        if latest_snap is None:
            return False, None

        # Scope the trailing window to THAT snapshot's contract (symbol,
        # expiry_date). A symbol can have several concurrent expiries live at
        # once; blending their yields into one mean is comparing apples to
        # oranges and would make the rule fire (or not) for the wrong reason.
        rows = (
            db.query(SLBSnapshot)
            .filter(
                SLBSnapshot.symbol == symbol,
                SLBSnapshot.series_family == family,
                SLBSnapshot.expiry_date == latest_snap.expiry_date,
                SLBSnapshot.annualized_yield.isnot(None),
            )
            .order_by(SLBSnapshot.timestamp.desc(), SLBSnapshot.id.desc())
            .limit(lookback)
            .all()
        )
        if len(rows) < _MIN_SAMPLES_FOR_AVERAGE:
            return False, None
        yields = [to_decimal(r.annualized_yield) for r in rows]
        latest = yields[0]
        avg = sum(yields) / to_decimal(len(yields))
        bar = avg * (to_decimal(1) + to_decimal(threshold_margin_pct) / to_decimal(100))
        if latest >= bar:
            return True, (
                f"{symbol} latest yield {float(latest):.2f}% ≥ {threshold_margin_pct:.0f}% "
                f"above its {len(yields)}-sample trailing average {float(avg):.2f}% "
                f"(bar {float(bar):.2f}%)."
            )
        return False, None

    @classmethod
    def _eval_expiry_reminder(
        cls, db: Session, alert: Alert, threshold_days: float, today: datetime.date
    ) -> tuple[bool, Optional[str]]:
        """Fire when an open contract is within ``threshold_days`` of expiry.

        Window: ``0 <= days_remaining <= threshold_days`` (not yet expired, on or
        before the reminder horizon). Uses derived, IST-based days_remaining.
        """
        window = int(threshold_days)
        views = ContractService.list_contracts(db=db, today=today)
        if alert.stock_id is not None:
            views = [v for v in views if v.stock_id == alert.stock_id]
        due = [
            v
            for v in views
            if v.effective_status not in ("EXPIRED",)
            and 0 <= v.days_remaining <= window
            and v.stored_status not in ("RECALLED", "CLOSED", "ROLLED_OVER")
        ]
        if not due:
            return False, None
        due.sort(key=lambda v: v.days_remaining)
        nearest = due[0]
        return True, (
            f"{len(due)} contract(s) within {window}d of expiry; nearest {nearest.symbol} "
            f"in {nearest.days_remaining}d ({nearest.expiry_date.isoformat()})."
        )

    @classmethod
    def _eval_rollover(
        cls, db: Session, alert: Alert, warn_days: float, today: datetime.date
    ) -> tuple[bool, Optional[str]]:
        """Fire when an open contract's reverse leg is within ``warn_days``
        (rollover_warn_days, default 5) and has not passed (Phase 4.5).

        The message names the nearest contract and the next Series B option at
        its bid (from the rollover advisor), or says the roll is blocked by the
        365-day cap or that no later bid has been captured.
        """
        from slbm_tracker.services.contract_service import TERMINAL_STATUSES
        from slbm_tracker.services.rollover_service import STATUS_BLOCKED_BY_CAP, RolloverService

        views = ContractService.list_contracts(db=db, today=today)
        if alert.stock_id is not None:
            views = [v for v in views if v.stock_id == alert.stock_id]

        def days_to_settle(v) -> int:
            return ((v.reverse_leg_date or v.expiry_date) - today).days

        due = [
            v for v in views
            if v.stored_status not in TERMINAL_STATUSES and 0 <= days_to_settle(v) <= warn_days
        ]
        if not due:
            return False, None
        due.sort(key=days_to_settle)
        v = due[0]
        rec = RolloverService._build(db, v, today)
        settles = (v.reverse_leg_date or v.expiry_date).strftime("%d %b %Y")
        head = f"{v.symbol} {v.series_code or v.series or '—'} settles {settles} ({days_to_settle(v)} days)"
        best = rec.best_option
        if rec.status == STATUS_BLOCKED_BY_CAP:
            tail = "roll blocked by the 365-day cap"
        elif best is None or best.best_bid is None:
            tail = "no later Series B bid captured"
        else:
            net = f", nets {best.net_yield_pa}% p.a." if best.net_yield_pa is not None else ""
            tail = f"next {best.series_code} bid ₹{best.best_bid:.2f} ({best.best_bid_qty:,}){net}"
        more = f" (+{len(due) - 1} more)" if len(due) > 1 else ""
        return True, f"{head}: {tail}; {rec.days_used_label}{more}."

    @classmethod
    def _eval_lending_opportunity(
        cls,
        db: Session,
        alert: Alert,
        floor_pct: float,
        today: datetime.date,
        now: Optional[datetime.datetime],
    ) -> tuple[bool, Optional[str]]:
        """Fire when any held stock (or the scoped one) has an actionable Series B
        option: live bid, fill, net yield at or above ``floor_pct``, net fee > 0,
        no corporate action in the period, quote under 24 hours old (Phase 3.6).
        The message carries the best option's ticket line."""
        from slbm_tracker.services import opportunity_service

        board = opportunity_service.build(
            db, today=today, now=now, min_net_yield_pct=floor_pct, symbol=cls._symbol_for(db, alert)
        )
        if alert.stock_id is not None and cls._symbol_for(db, alert) is None:
            return False, None
        best = opportunity_service.actionable_options(board)
        if not best:
            return False, None
        top = best[0]
        more = f" (+{len(best) - 1} more)" if len(best) > 1 else ""
        return True, (
            f"{top['symbol']} {top['series_code']} nets {top['net_yield_pa']}% p.a. at the bid "
            f"(lend {top['fillable']}, net ₹{top['net_fee']}){more}. Ticket: {top['ticket']}"
        )

    @classmethod
    def _evaluate_one(
        cls,
        db: Session,
        alert: Alert,
        today: datetime.date,
        now: Optional[datetime.datetime] = None,
    ) -> tuple[bool, Optional[str], float]:
        """Dispatch to the per-type evaluator. Returns (met, message, threshold)."""
        thr = cls._threshold_for(alert)
        if alert.alert_type == ALERT_YIELD_THRESHOLD:
            met, msg = cls._eval_yield_threshold(db, alert, thr, today)
        elif alert.alert_type == ALERT_TIGHT_SPREAD:
            met, msg = cls._eval_tight_spread(db, alert, thr, today)
        elif alert.alert_type == ALERT_ABOVE_AVERAGE:
            met, msg = cls._eval_above_average(db, alert, thr)
        elif alert.alert_type == ALERT_EXPIRY_REMINDER:
            met, msg = cls._eval_expiry_reminder(db, alert, thr, today)
        elif alert.alert_type == ALERT_ROLLOVER:
            met, msg = cls._eval_rollover(db, alert, thr, today)
        elif alert.alert_type == ALERT_LENDING_OPPORTUNITY:
            met, msg = cls._eval_lending_opportunity(db, alert, thr, today, now)
        else:  # defensive: unknown type never fires
            logger.warning("Skipping unknown alert_type %s (id=%s)", alert.alert_type, alert.id)
            met, msg = False, None
        return met, msg, thr

    @classmethod
    def evaluate_all(
        cls,
        db: Session,
        today: Optional[datetime.date] = None,
        now: Optional[datetime.datetime] = None,
    ) -> list[AlertEvaluation]:
        """Evaluate every ACTIVE alert rule; persist edge-triggered state.

        Triggers are committed HERE, before any delivery is attempted, so a
        later delivery failure cannot roll back or destroy a trigger.
        """
        today = today or today_ist()
        # Naive UTC: the trigger timestamp and the opportunity quote-age clock.
        now = now or datetime.datetime.utcnow()
        alerts = cls.list_alerts(db, active_only=True)
        results: list[AlertEvaluation] = []

        cooldown = datetime.timedelta(minutes=int(business_config.effective("alert_cooldown_minutes")))
        for alert in alerts:
            met, message, thr = cls._evaluate_one(db, alert, today, now)
            fired_now = met and not alert.is_triggered
            # Phase 5.4: a re-crossing within the rule's cooldown since its last
            # fire is held as triggered but sends nothing new.
            suppressed = bool(
                fired_now
                and cooldown
                and alert.last_triggered_at is not None
                and now - alert.last_triggered_at < cooldown
            )
            if suppressed:
                fired_now = False
                alert.is_triggered = True
            if met:
                if fired_now:
                    alert.is_triggered = True
                    alert.last_triggered_at = now
                    alert.trigger_message = message
                # else: still holding -> dedup, leave timestamp/message intact
            else:
                # Re-arm; keep historical last_triggered_at for the record.
                alert.is_triggered = False
            results.append(
                AlertEvaluation(
                    alert_id=alert.id,
                    alert_type=alert.alert_type,
                    stock_id=alert.stock_id,
                    threshold_used=thr,
                    condition_met=met,
                    fired_now=fired_now,
                    message=message,
                    cooldown_suppressed=suppressed,
                )
            )

        db.commit()
        return results
