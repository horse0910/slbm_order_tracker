"""Unit tests for RolloverService.

Key guarantees under test (Phase 3 scope):
* No future-series data is invented — INSUFFICIENT_DATA when none is persisted.
* Only *later*-expiry snapshots of the SAME symbol are considered.
* The best option is chosen from real snapshot data (yield-first).
* fee_delta / favorable are factual per-share comparisons, not forecasts.
"""

from __future__ import annotations

import datetime

from sqlalchemy.orm import Session

from slbm_tracker.db.models import SLBSnapshot, Stock
from slbm_tracker.services.contract_service import ContractService
from slbm_tracker.services.rollover_service import (
    STATUS_INSUFFICIENT_DATA,
    STATUS_ROLL_AVAILABLE,
    RolloverService,
)

TODAY = datetime.date(2026, 10, 30)  # 4 days before the Nov-3 expiry -> candidate
CURRENT_EXPIRY = datetime.date(2026, 11, 3)


def _make_stock(db: Session, symbol: str = "RELIANCE") -> Stock:
    stock = Stock(symbol=symbol, quantity=1000, avg_buy_price=2500.0)
    db.add(stock)
    db.commit()
    db.refresh(stock)
    return stock


def _make_candidate_contract(db: Session, stock: Stock, fee: float = 5.0):
    return ContractService.create_contract(
        db=db,
        stock_id=stock.id,
        expiry_date=CURRENT_EXPIRY,
        quantity_lent=100,
        lending_fee=fee,
        series="Nov-2026",
        trade_date=datetime.date(2026, 10, 1),
    )


def _add_snapshot(
    db: Session,
    symbol: str,
    expiry: datetime.date,
    *,
    series: str,
    ltp: float | None,
    yield_pct: float | None,
    ts: datetime.datetime,
):
    # Since Phase 4.4 options are Series B rows priced at the bid; these
    # fixtures bid at the last traded fee (no bid when there is no fee).
    codes = {"Nov": "XN", "Dec": "XD", "Jan": "X1", "Feb": "X2"}
    snap = SLBSnapshot(series_family="B",
        series_code=codes[series[:3]],
        best_bid=ltp,
        best_bid_qty=5000 if ltp else 0,
        underlying_price=2500.0,
        symbol=symbol,
        series=series,
        expiry_date=expiry,
        ltp=ltp,
        annualized_yield=yield_pct,
        open_positions=5000,
        timestamp=ts,
    )
    db.add(snap)
    db.commit()
    return snap


def test_no_future_data_is_insufficient(db: Session):
    stock = _make_stock(db)
    _make_candidate_contract(db, stock)
    recs = RolloverService.advise(db=db, today=TODAY)
    assert len(recs) == 1
    assert recs[0].status == STATUS_INSUFFICIENT_DATA
    assert recs[0].best_option is None
    assert recs[0].fee_delta is None
    assert recs[0].favorable is None
    assert "No persisted later Series B data" in recs[0].rationale


def test_only_later_expiry_same_symbol_considered(db: Session):
    stock = _make_stock(db)
    _make_candidate_contract(db, stock)
    ts = datetime.datetime(2026, 10, 29, 12, 0, 0)
    # Same-expiry snapshot (not a future series) -> ignored.
    _add_snapshot(db, "RELIANCE", CURRENT_EXPIRY, series="Nov-2026", ltp=6.0, yield_pct=9.0, ts=ts)
    # Different symbol future -> ignored.
    _add_snapshot(db, "TCS", datetime.date(2026, 12, 1), series="Dec-2026", ltp=7.0, yield_pct=9.5, ts=ts)
    recs = RolloverService.advise(db=db, today=TODAY)
    assert recs[0].status == STATUS_INSUFFICIENT_DATA


def test_picks_best_future_by_yield_and_flags_favorable(db: Session):
    stock = _make_stock(db)
    _make_candidate_contract(db, stock, fee=5.0)
    ts = datetime.datetime(2026, 10, 29, 12, 0, 0)
    # Two future series; Dec has the higher yield -> chosen as best.
    _add_snapshot(db, "RELIANCE", datetime.date(2026, 12, 1), series="Dec-2026", ltp=6.5, yield_pct=9.4, ts=ts)
    _add_snapshot(db, "RELIANCE", datetime.date(2027, 1, 5), series="Jan-2027", ltp=6.0, yield_pct=8.9, ts=ts)
    recs = RolloverService.advise(db=db, today=TODAY)
    rec = recs[0]
    assert rec.status == STATUS_ROLL_AVAILABLE
    assert rec.best_option.series == "Dec-2026"
    # 6.5 - 5.0 = 1.5 (favorable).
    assert round(rec.fee_delta, 4) == 1.5
    assert rec.favorable is True
    assert len(rec.options) == 2


def test_below_current_fee_is_not_favorable(db: Session):
    stock = _make_stock(db)
    _make_candidate_contract(db, stock, fee=8.0)
    ts = datetime.datetime(2026, 10, 29, 12, 0, 0)
    _add_snapshot(db, "RELIANCE", datetime.date(2026, 12, 1), series="Dec-2026", ltp=6.5, yield_pct=9.4, ts=ts)
    rec = RolloverService.advise(db=db, today=TODAY)[0]
    assert rec.favorable is False
    assert round(rec.fee_delta, 4) == -1.5
    assert "below the current" in rec.rationale


def test_latest_snapshot_per_series_wins(db: Session):
    stock = _make_stock(db)
    _make_candidate_contract(db, stock, fee=5.0)
    older = datetime.datetime(2026, 10, 20, 9, 0, 0)
    newer = datetime.datetime(2026, 10, 29, 15, 30, 0)
    # Same future series, two captures: the newer one should be used.
    _add_snapshot(db, "RELIANCE", datetime.date(2026, 12, 1), series="Dec-2026", ltp=6.0, yield_pct=8.0, ts=older)
    _add_snapshot(db, "RELIANCE", datetime.date(2026, 12, 1), series="Dec-2026", ltp=7.0, yield_pct=9.0, ts=newer)
    rec = RolloverService.advise(db=db, today=TODAY)[0]
    assert len(rec.options) == 1
    assert rec.best_option.market_fee == 7.0
    assert rec.best_option.data_as_of == newer


def test_future_series_without_traded_fee_is_not_comparable(db: Session):
    stock = _make_stock(db)
    _make_candidate_contract(db, stock, fee=5.0)
    ts = datetime.datetime(2026, 10, 29, 12, 0, 0)
    _add_snapshot(db, "RELIANCE", datetime.date(2026, 12, 1), series="Dec-2026", ltp=None, yield_pct=None, ts=ts)
    rec = RolloverService.advise(db=db, today=TODAY)[0]
    assert rec.status == STATUS_ROLL_AVAILABLE
    assert rec.favorable is None
    assert rec.fee_delta is None
    assert "no live bid" in rec.rationale


def test_non_candidate_contract_excluded(db: Session):
    stock = _make_stock(db)
    # Far-future expiry -> not a rollover candidate -> no recommendation.
    ContractService.create_contract(
        db=db,
        stock_id=stock.id,
        expiry_date=datetime.date(2027, 6, 1),
        quantity_lent=100,
        lending_fee=5.0,
        trade_date=datetime.date(2026, 10, 1),
    )
    recs = RolloverService.advise(db=db, today=TODAY)
    assert recs == []
