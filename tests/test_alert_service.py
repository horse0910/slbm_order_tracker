"""Unit tests for the alert rule engine: rule CRUD, deterministic evaluation,
threshold boundary conditions (>= vs <=), and edge-triggered dedup.

Boundary conditions are exercised with explicit thresholds and explicit
snapshot data so they are deterministic and independent of the machine clock.
"""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy.orm import Session

from slbm_tracker.config import get_settings
from slbm_tracker.db.models import Alert, SLBSnapshot, Stock
from slbm_tracker.services.alert_service import (
    ALERT_ABOVE_AVERAGE,
    ALERT_EXPIRY_REMINDER,
    ALERT_ROLLOVER,
    ALERT_TIGHT_SPREAD,
    ALERT_YIELD_THRESHOLD,
    AlertService,
)
from slbm_tracker.services.contract_service import ContractService

pytestmark = pytest.mark.usefixtures("pinned_snapshot_today")


def _stock(db: Session, symbol: str = "RELIANCE") -> Stock:
    stock = Stock(symbol=symbol, quantity=1000, avg_buy_price=2500.0)
    db.add(stock)
    db.commit()
    db.refresh(stock)
    return stock


def _snapshot(
    db: Session,
    *,
    symbol: str = "RELIANCE",
    series: str = "Nov-2026",
    expiry: datetime.date = datetime.date(2026, 11, 3),
    annualized_yield: float | None = None,
    best_bid: float | None = None,
    best_ask: float | None = None,
    ts: datetime.datetime | None = None,
) -> SLBSnapshot:
    snap = SLBSnapshot(series_family="B", 
        symbol=symbol,
        series=series,
        expiry_date=expiry,
        annualized_yield=annualized_yield,
        best_bid=best_bid,
        best_ask=best_ask,
        timestamp=ts or datetime.datetime.utcnow(),
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)
    return snap


# --- rule CRUD -----------------------------------------------------------

def test_create_alert_validates_type(db: Session):
    with pytest.raises(ValueError, match="Unknown alert_type"):
        AlertService.create_alert(db=db, alert_type="NONSENSE")


def test_create_alert_validates_stock(db: Session):
    with pytest.raises(ValueError, match="not found"):
        AlertService.create_alert(db=db, alert_type=ALERT_YIELD_THRESHOLD, stock_id=999)


# --- YIELD_THRESHOLD boundary: >= (at threshold FIRES) -------------------

def test_yield_threshold_exactly_at_threshold_fires(db: Session):
    _snapshot(db, annualized_yield=8.0)
    AlertService.create_alert(db=db, alert_type=ALERT_YIELD_THRESHOLD, threshold=8.0)
    results = AlertService.evaluate_all(db=db)
    assert results[0].condition_met is True  # 8.0 >= 8.0


def test_yield_threshold_just_below_does_not_fire(db: Session):
    _snapshot(db, annualized_yield=7.99)
    AlertService.create_alert(db=db, alert_type=ALERT_YIELD_THRESHOLD, threshold=8.0)
    results = AlertService.evaluate_all(db=db)
    assert results[0].condition_met is False  # 7.99 < 8.0


def test_yield_threshold_just_above_fires(db: Session):
    _snapshot(db, annualized_yield=8.01)
    AlertService.create_alert(db=db, alert_type=ALERT_YIELD_THRESHOLD, threshold=8.0)
    results = AlertService.evaluate_all(db=db)
    assert results[0].condition_met is True


def test_yield_threshold_no_snapshot_does_not_fire(db: Session):
    AlertService.create_alert(db=db, alert_type=ALERT_YIELD_THRESHOLD, threshold=8.0)
    results = AlertService.evaluate_all(db=db)
    assert results[0].condition_met is False  # never fabricates data


# --- TIGHT_SPREAD boundary: <= (at threshold FIRES) ----------------------
# spread% = (ask - bid) / ask * 100. bid=95, ask=100 -> 5.0%.

def test_tight_spread_exactly_at_threshold_fires(db: Session):
    _snapshot(db, best_bid=95.0, best_ask=100.0)  # 5.0%
    AlertService.create_alert(db=db, alert_type=ALERT_TIGHT_SPREAD, threshold=5.0)
    results = AlertService.evaluate_all(db=db)
    assert results[0].condition_met is True  # 5.0 <= 5.0


def test_tight_spread_just_above_threshold_does_not_fire(db: Session):
    _snapshot(db, best_bid=94.0, best_ask=100.0)  # 6.0%
    AlertService.create_alert(db=db, alert_type=ALERT_TIGHT_SPREAD, threshold=5.0)
    results = AlertService.evaluate_all(db=db)
    assert results[0].condition_met is False  # 6.0 > 5.0


def test_tight_spread_missing_ask_is_skipped(db: Session):
    _snapshot(db, best_bid=95.0, best_ask=None)
    AlertService.create_alert(db=db, alert_type=ALERT_TIGHT_SPREAD, threshold=5.0)
    results = AlertService.evaluate_all(db=db)
    assert results[0].condition_met is False


# --- ABOVE_AVERAGE -------------------------------------------------------

def test_above_average_needs_two_samples(db: Session):
    stock = _stock(db)
    _snapshot(db, annualized_yield=12.0)  # single sample only
    AlertService.create_alert(
        db=db, alert_type=ALERT_ABOVE_AVERAGE, threshold=20.0, stock_id=stock.id
    )
    results = AlertService.evaluate_all(db=db)
    assert results[0].condition_met is False  # not enough history


def test_above_average_fires_when_latest_beats_avg_by_margin(db: Session):
    stock = _stock(db)
    # Older low yields then a high latest. avg of [6,6,12]=8; bar=8*1.2=9.6; latest 12 >= 9.6.
    base = datetime.datetime(2026, 10, 1, 9, 0, 0)
    _snapshot(db, annualized_yield=6.0, ts=base)
    _snapshot(db, annualized_yield=6.0, ts=base + datetime.timedelta(days=1))
    _snapshot(db, annualized_yield=12.0, ts=base + datetime.timedelta(days=2))
    AlertService.create_alert(
        db=db, alert_type=ALERT_ABOVE_AVERAGE, threshold=20.0, stock_id=stock.id
    )
    results = AlertService.evaluate_all(db=db)
    assert results[0].condition_met is True


def test_above_average_uses_only_the_trailing_window(db: Session, monkeypatch):
    """The average is the mean of the last N snapshots, not of ALL history.

    Regression guard for the Phase-4 trailing-window fix. The out-of-window rows
    here are sky-high; if they were folded into the mean (the old all-history
    behavior that made the rule fire less and less as data accrued), the latest
    reading would fall far below the bar and NOT fire. With the window bounded to
    the 3 most recent samples it fires on the recent baseline.
    """
    monkeypatch.setattr(get_settings(), "above_average_lookback_samples", 3)
    stock = _stock(db)
    base = datetime.datetime(2026, 10, 1, 9, 0, 0)
    # OLD, out-of-window, sky-high readings (would wreck an all-history average).
    _snapshot(db, annualized_yield=100.0, ts=base)
    _snapshot(db, annualized_yield=100.0, ts=base + datetime.timedelta(minutes=1))
    # RECENT 3-sample trailing window: low, flat baseline then a latest spike.
    _snapshot(db, annualized_yield=6.0, ts=base + datetime.timedelta(minutes=10))
    _snapshot(db, annualized_yield=6.0, ts=base + datetime.timedelta(minutes=11))
    _snapshot(db, annualized_yield=12.0, ts=base + datetime.timedelta(minutes=12))  # latest
    AlertService.create_alert(
        db=db, alert_type=ALERT_ABOVE_AVERAGE, threshold=20.0, stock_id=stock.id
    )
    results = AlertService.evaluate_all(db=db)
    # window=[12,6,6] -> avg 8, bar 9.6, latest 12 >= 9.6 -> fires.
    assert results[0].condition_met is True


def test_above_average_ignores_stale_lows_outside_window(db: Session, monkeypatch):
    """Mirror guard: out-of-window LOW readings must not drag the trailing mean
    down enough to manufacture a fire the recent data alone would not justify."""
    monkeypatch.setattr(get_settings(), "above_average_lookback_samples", 3)
    stock = _stock(db)
    base = datetime.datetime(2026, 10, 1, 9, 0, 0)
    # OLD, out-of-window near-zero readings.
    _snapshot(db, annualized_yield=0.1, ts=base)
    _snapshot(db, annualized_yield=0.1, ts=base + datetime.timedelta(minutes=1))
    # RECENT window: high and flat -> latest does NOT beat its own recent average.
    _snapshot(db, annualized_yield=12.0, ts=base + datetime.timedelta(minutes=10))
    _snapshot(db, annualized_yield=12.0, ts=base + datetime.timedelta(minutes=11))
    _snapshot(db, annualized_yield=12.0, ts=base + datetime.timedelta(minutes=12))  # latest
    AlertService.create_alert(
        db=db, alert_type=ALERT_ABOVE_AVERAGE, threshold=20.0, stock_id=stock.id
    )
    results = AlertService.evaluate_all(db=db)
    # window=[12,12,12] -> avg 12, bar 14.4, latest 12 < 14.4 -> no fire.
    # If the stale 0.1s were folded in, the mean would crater and it WOULD fire.
    assert results[0].condition_met is False


def test_above_average_scopes_window_to_latest_snapshots_expiry(db: Session, monkeypatch):
    """MED M3: the trailing window is scoped to the latest snapshot's
    (symbol, expiry_date) — never blended across a symbol's concurrent expiries.

    The reference 'latest' reading is the most recent snapshot for the symbol
    (a Nov-expiry spike). A DIFFERENT expiry (Dec) carries older sky-high yields;
    if those were folded into the mean (the old symbol-only window) the bar would
    tower over the latest and the rule would NOT fire. Scoped to the Nov contract
    alone it fires on the recent baseline.
    """
    monkeypatch.setattr(get_settings(), "above_average_lookback_samples", 5)
    stock = _stock(db)
    nov = datetime.date(2026, 11, 3)
    dec = datetime.date(2026, 12, 1)
    base = datetime.datetime(2026, 10, 1, 9, 0, 0)
    # OTHER expiry (Dec), older + sky-high — must be excluded by expiry scoping.
    _snapshot(db, series="Dec-2026", expiry=dec, annualized_yield=100.0, ts=base)
    _snapshot(db, series="Dec-2026", expiry=dec, annualized_yield=100.0,
              ts=base + datetime.timedelta(minutes=1))
    # Reference expiry (Nov): recent low baseline then the latest spike.
    _snapshot(db, expiry=nov, annualized_yield=6.0, ts=base + datetime.timedelta(minutes=10))
    _snapshot(db, expiry=nov, annualized_yield=6.0, ts=base + datetime.timedelta(minutes=11))
    _snapshot(db, expiry=nov, annualized_yield=12.0, ts=base + datetime.timedelta(minutes=12))
    AlertService.create_alert(
        db=db, alert_type=ALERT_ABOVE_AVERAGE, threshold=20.0, stock_id=stock.id
    )
    results = AlertService.evaluate_all(db=db)
    # Nov window [12,6,6] -> avg 8, bar 9.6, latest 12 >= 9.6 -> fires.
    # Blended [12,6,6,100,100] -> avg 44.8, bar 53.76 -> would NOT fire.
    assert results[0].condition_met is True


def test_above_average_latest_is_deterministic_under_tied_timestamps(db: Session, monkeypatch):
    """MED M3: when the newest snapshots share a timestamp, 'latest' is the
    highest id (deterministic), not an arbitrary tied row.

    Two snapshots carry the same max timestamp: a low 7.0 and a high 20.0. The
    outcome hinges on which is treated as 'latest': the high one (id.desc, the
    fix) clears the bar and fires; the low one would fall below it. Tied
    timestamps are the norm under the Phase 4 scheduler (one utcnow() per tick).
    """
    monkeypatch.setattr(get_settings(), "above_average_lookback_samples", 5)
    stock = _stock(db)
    base = datetime.datetime(2026, 10, 1, 9, 0, 0)
    _snapshot(db, annualized_yield=6.0, ts=base)
    _snapshot(db, annualized_yield=6.0, ts=base + datetime.timedelta(minutes=1))
    tied = base + datetime.timedelta(minutes=2)
    _snapshot(db, annualized_yield=7.0, ts=tied)   # lower id, tied timestamp
    _snapshot(db, annualized_yield=20.0, ts=tied)  # higher id, tied timestamp -> latest
    AlertService.create_alert(
        db=db, alert_type=ALERT_ABOVE_AVERAGE, threshold=20.0, stock_id=stock.id
    )
    results = AlertService.evaluate_all(db=db)
    # window [20,7,6,6] -> avg 9.75, bar 11.7; latest=20 (id.desc) >= 11.7 -> fires.
    # If 'latest' were the tied 7.0, 7 < 11.7 -> would not fire.
    assert results[0].condition_met is True

def _contract(db: Session, stock_id: int, expiry: datetime.date, trade: datetime.date):
    return ContractService.create_contract(
        db=db, stock_id=stock_id, expiry_date=expiry, quantity_lent=10,
        lending_fee=5.0, trade_date=trade,
    )


def test_expiry_reminder_fires_inside_window(db: Session):
    stock = _stock(db)
    today = datetime.date(2026, 10, 30)
    _contract(db, stock.id, datetime.date(2026, 11, 3), datetime.date(2026, 10, 1))  # 4d out
    AlertService.create_alert(db=db, alert_type=ALERT_EXPIRY_REMINDER, threshold=7)
    results = AlertService.evaluate_all(db=db, today=today)
    assert results[0].condition_met is True


def test_expiry_reminder_does_not_fire_outside_window(db: Session):
    stock = _stock(db)
    today = datetime.date(2026, 10, 1)
    _contract(db, stock.id, datetime.date(2026, 12, 1), datetime.date(2026, 10, 1))  # far out
    AlertService.create_alert(db=db, alert_type=ALERT_EXPIRY_REMINDER, threshold=7)
    results = AlertService.evaluate_all(db=db, today=today)
    assert results[0].condition_met is False


def test_rollover_alert_fires_for_candidate(db: Session):
    stock = _stock(db)
    today = datetime.date(2026, 10, 30)
    _contract(db, stock.id, datetime.date(2026, 11, 3), datetime.date(2026, 10, 1))  # candidate
    AlertService.create_alert(db=db, alert_type=ALERT_ROLLOVER)
    results = AlertService.evaluate_all(db=db, today=today)
    assert results[0].condition_met is True


# --- edge-triggered dedup ------------------------------------------------

def test_edge_triggered_dedup_does_not_refire(db: Session):
    _snapshot(db, annualized_yield=9.0)
    AlertService.create_alert(db=db, alert_type=ALERT_YIELD_THRESHOLD, threshold=8.0)

    first = AlertService.evaluate_all(db=db)
    assert first[0].fired_now is True  # transition into triggered
    row = db.query(Alert).first()
    stamp = row.last_triggered_at
    assert row.is_triggered is True

    second = AlertService.evaluate_all(db=db)
    assert second[0].condition_met is True
    assert second[0].fired_now is False  # dedup: still holding, no re-fire
    db.refresh(row)
    assert row.last_triggered_at == stamp  # timestamp unchanged


def test_rearm_when_condition_clears(db: Session):
    snap = _snapshot(db, annualized_yield=9.0)
    AlertService.create_alert(db=db, alert_type=ALERT_YIELD_THRESHOLD, threshold=8.0)
    AlertService.evaluate_all(db=db)
    row = db.query(Alert).first()
    assert row.is_triggered is True

    # Yield drops below threshold -> re-arm.
    snap.annualized_yield = 5.0
    db.commit()
    results = AlertService.evaluate_all(db=db)
    assert results[0].condition_met is False
    db.refresh(row)
    assert row.is_triggered is False  # re-armed
    assert row.last_triggered_at is not None  # historical stamp preserved


def test_inactive_rules_are_not_evaluated(db: Session):
    _snapshot(db, annualized_yield=9.0)
    AlertService.create_alert(
        db=db, alert_type=ALERT_YIELD_THRESHOLD, threshold=8.0, is_active=False
    )
    results = AlertService.evaluate_all(db=db)
    assert results == []
