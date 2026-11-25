"""Rollover Advisor: decision-support for contracts nearing expiry.

This service is DECISION-SUPPORT, not order execution and not a prediction
engine (Phase 3 scope control). It never invents future-series market data:
every future-series figure it reports comes from a *persisted* SLB market
snapshot (:class:`SLBSnapshot`) captured earlier from the exchange. When no
such data exists for a symbol, the case is marked ``INSUFFICIENT_DATA`` rather
than guessing.

For each contract that is a rollover candidate (non-terminal and at/inside the
expiring-soon window — see :class:`ContractService`), the advisor looks for the
most recent snapshot of each *later* series/expiry of the same symbol and
surfaces:

* the available future series, their last-traded lending fee (per share),
  annualized yield and the timestamp the data was captured, and
* a purely factual comparison of the best available future fee against the
  current contract's fee (``fee_delta`` / ``favorable``). This is arithmetic on
  real data, not a forecast — the user makes the decision.

Nothing here places or simulates an order.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from slbm_tracker.config import get_settings
from slbm_tracker.db.models import SLBSnapshot
from decimal import Decimal

from slbm_tracker.services import lending_economics, opportunity_service
from slbm_tracker.services.contract_service import MAX_CHAIN_DAYS, ContractService, today_ist
from slbm_tracker.utils.money import to_decimal

logger = logging.getLogger(__name__)

# Advisor status for a candidate contract.
STATUS_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"  # no persisted later Series B data
STATUS_ROLL_AVAILABLE = "ROLL_AVAILABLE"  # at least one later series fits the cap
STATUS_BLOCKED_BY_CAP = "BLOCKED_BY_CAP"  # later series exist, all past the 365-day cap


@dataclass(frozen=True)
class RolloverOption:
    """A later Series B series the contract could roll into (Phase 4.4).

    Priced at the persisted best BID on the fillable quantity (the contract's
    shares, capped by the bid quantity), net of Zerodha's charges, over the days
    from the current settlement to this one. ``market_fee`` is the last traded
    fee, shown for reference only; decisions use the bid.
    """

    series: Optional[str]
    expiry_date: datetime.date
    market_fee: Optional[float]  # per share (snapshot ltp), reference only
    annualized_yield: Optional[float]  # NSE's gross figure, reference only
    open_positions: Optional[int]
    data_as_of: datetime.datetime  # snapshot timestamp (when the data was captured)
    series_code: Optional[str] = None
    best_bid: Optional[float] = None
    best_bid_qty: int = 0
    fillable: int = 0
    net_fee: Optional[Decimal] = None
    net_yield_pa: Optional[Decimal] = None
    chain_days: int = 0  # this settlement - origin_date
    within_cap: bool = True


@dataclass(frozen=True)
class RolloverRecommendation:
    """Decision-support summary for one rollover-candidate contract."""

    contract_id: int
    stock_id: int
    symbol: str
    current_series: Optional[str]
    current_expiry: datetime.date
    current_fee: float  # per share
    days_remaining: int  # signed
    status: str  # INSUFFICIENT_DATA | ROLL_AVAILABLE | BLOCKED_BY_CAP
    rationale: str
    best_option: Optional[RolloverOption]
    # Best option's BID vs the current fee (both per share).
    fee_delta: Optional[float]  # best_option.best_bid - current_fee, or None
    favorable: Optional[bool]  # fee_delta >= 0, or None when not comparable
    options: list[RolloverOption] = field(default_factory=list)
    origin_date: Optional[datetime.date] = None
    days_used: Optional[int] = None  # today - origin_date
    days_used_label: Optional[str] = None  # "N of 365 days used"


class RolloverService:
    """Build rollover decision-support from contracts + persisted snapshots."""

    @classmethod
    def _future_options(
        cls,
        db: Session,
        symbol: str,
        after_settlement: datetime.date,
        quantity: int,
        origin: datetime.date,
    ) -> list[RolloverOption]:
        """Latest Series B snapshot per series code settling after the current one,
        priced at the bid. Within-cap options with a bid come first, best net
        yield first; options past the cap come last."""
        rows = (
            db.query(SLBSnapshot)
            .filter(
                SLBSnapshot.symbol == symbol,
                SLBSnapshot.expiry_date > after_settlement,
                SLBSnapshot.series_family == get_settings().series_family,
                SLBSnapshot.series_code.isnot(None),
            )
            .order_by(SLBSnapshot.timestamp.desc(), SLBSnapshot.id.desc())
            .all()
        )
        latest: dict[str, SLBSnapshot] = {}
        for snap in rows:  # newest first, so first seen wins
            latest.setdefault(snap.series_code, snap)

        spot = opportunity_service.last_price(db, symbol)
        charges = lending_economics.Charges.from_config()
        options = []
        for s in latest.values():
            settles = s.settlement_date or s.expiry_date
            bid = s.best_bid or 0.0
            bid_qty = int(s.best_bid_qty or 0)
            fillable = max(min(quantity, bid_qty), 0)
            econ = (
                lending_economics.evaluate(fillable, bid, spot or 0, (settles - after_settlement).days, charges)
                if fillable > 0 and bid > 0
                else None
            )
            chain_days = (settles - origin).days
            options.append(
                RolloverOption(
                    series=s.series,
                    expiry_date=settles,
                    market_fee=s.ltp,
                    annualized_yield=s.annualized_yield,
                    open_positions=s.open_positions,
                    data_as_of=s.timestamp,
                    series_code=s.series_code,
                    best_bid=bid or None,
                    best_bid_qty=bid_qty,
                    fillable=fillable,
                    net_fee=econ.net_fee if econ else None,
                    net_yield_pa=econ.net_yield_pa if econ else None,
                    chain_days=chain_days,
                    within_cap=chain_days <= MAX_CHAIN_DAYS,
                )
            )
        options.sort(
            key=lambda o: (
                not o.within_cap,
                o.best_bid is None,
                -(o.net_yield_pa if o.net_yield_pa is not None else Decimal("-1e9")),
                o.expiry_date,
            )
        )
        return options

    @classmethod
    def _build(
        cls, db: Session, view, today: datetime.date
    ) -> RolloverRecommendation:
        origin = view.origin_date or view.trade_date
        current_settlement = view.reverse_leg_date or view.expiry_date
        days_used = max((today - origin).days, 0)
        options = cls._future_options(db, view.symbol, current_settlement, view.quantity_lent, origin)
        common = dict(
            contract_id=view.id,
            stock_id=view.stock_id,
            symbol=view.symbol,
            current_series=view.series,
            current_expiry=view.expiry_date,
            current_fee=view.lending_fee,
            days_remaining=view.days_remaining,
            options=options,
            origin_date=origin,
            days_used=days_used,
            days_used_label=f"{days_used} of {MAX_CHAIN_DAYS} days used",
        )

        if not options:
            return RolloverRecommendation(
                status=STATUS_INSUFFICIENT_DATA,
                rationale=(
                    f"No persisted later Series B data for {view.symbol}. "
                    "It appears after the next sweep in market hours."
                ),
                best_option=None,
                fee_delta=None,
                favorable=None,
                **common,
            )

        within = [o for o in options if o.within_cap]
        if not within:
            return RolloverRecommendation(
                status=STATUS_BLOCKED_BY_CAP,
                rationale=(
                    f"Every later Series B series settles more than {MAX_CHAIN_DAYS} days after the original "
                    f"contract date {origin.isoformat()} (SEBI cap); this chain cannot be rolled. "
                    f"{days_used} of {MAX_CHAIN_DAYS} days used."
                ),
                best_option=None,
                fee_delta=None,
                favorable=None,
                **common,
            )

        best = within[0]
        fee_delta: Optional[float] = None
        favorable: Optional[bool] = None
        if best.best_bid is not None:
            delta = to_decimal(best.best_bid) - to_decimal(view.lending_fee)
            fee_delta = float(delta)
            favorable = delta >= 0

        if favorable is None:
            rationale = (
                f"{best.series_code} (settles {best.expiry_date.isoformat()}) has no live bid in the "
                "captured data; compare manually."
            )
        else:
            word = "at or above" if favorable else "below"
            net = f", net {best.net_yield_pa}% p.a. on {best.fillable}" if best.net_yield_pa is not None else ""
            rationale = (
                f"Best: {best.series_code} bid ₹{best.best_bid:.2f}/share ({word} the current "
                f"₹{view.lending_fee:.2f}){net}. Data as of {best.data_as_of.isoformat(timespec='minutes')}."
            )

        return RolloverRecommendation(
            status=STATUS_ROLL_AVAILABLE,
            rationale=rationale,
            best_option=best,
            fee_delta=fee_delta,
            favorable=favorable,
            **common,
        )

    @classmethod
    def advise_contract(
        cls, db: Session, contract_id: int, today: Optional[datetime.date] = None
    ) -> RolloverRecommendation:
        """Roll options for one open contract, whatever its days to expiry (the
        roll dialog). LookupError if unknown; ContractStateError if not open."""
        from slbm_tracker.services.contract_service import TERMINAL_STATUSES, ContractStateError

        today = today or today_ist()
        view = ContractService.get_contract_view(db=db, contract_id=contract_id, today=today)
        if view is None:
            raise LookupError(f"Contract {contract_id} not found.")
        if view.stored_status in TERMINAL_STATUSES:
            raise ContractStateError(f"Contract {contract_id} is {view.stored_status}; only an open contract can be rolled.")
        return cls._build(db, view, today)

    @classmethod
    def advise(
        cls, db: Session, today: Optional[datetime.date] = None
    ) -> list[RolloverRecommendation]:
        """Rollover decision-support for every rollover-candidate contract.

        Candidacy (non-terminal + inside the expiring-soon window, incl. already
        past expiry) is owned by :class:`ContractService`; this method never
        re-derives it. Ordered by urgency (fewest days remaining first).
        """
        today = today or today_ist()
        views = ContractService.list_contracts(db=db, today=today)
        candidates = [v for v in views if v.is_rollover_candidate]
        candidates.sort(key=lambda v: v.days_remaining)
        return [cls._build(db, v, today) for v in candidates]
