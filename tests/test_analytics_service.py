"""Tests for analytics roll-ups: FY income grouping (Decimal), derived status
breakdown, and per-symbol yield stats from persisted snapshots only.
"""

from __future__ import annotations

import datetime

from sqlalchemy.orm import Session

from slbm_tracker.db.models import SLBSnapshot, Stock
from slbm_tracker.services.analytics_service import AnalyticsService
from slbm_tracker.services.contract_service import ContractService
from slbm_tracker.services.pnl_service import PnLService


def _stock(db: Session, symbol: str = "RELIANCE") -> Stock:
    stock = Stock(symbol=symbol, quantity=1000, avg_buy_price=2500.0)
    db.add(stock)
    db.commit()
    db.refresh(stock)
    return stock


def test_income_by_financial_year_groups_and_sums(db: Session):
    stock = _stock(db)
    # Contract expiring in FY 2026-2027 (Nov 2026).
    c1 = ContractService.create_contract(
        db=db, stock_id=stock.id, expiry_date=datetime.date(2026, 11, 3),
        quantity_lent=100, lending_fee=5.0, trade_date=datetime.date(2026, 10, 1),
    )
    # Contract expiring in FY 2027-2028 (May 2027).
    c2 = ContractService.create_contract(
        db=db, stock_id=stock.id, expiry_date=datetime.date(2027, 5, 4),
        quantity_lent=100, lending_fee=5.0, trade_date=datetime.date(2027, 4, 1),
    )
    PnLService.record_for_contract(db=db, contract_id=c1.id)
    PnLService.record_for_contract(db=db, contract_id=c2.id)

    fy = AnalyticsService.income_by_financial_year(db)
    years = {r.financial_year: r for r in fy}
    assert "2026-2027" in years
    assert "2027-2028" in years
    assert years["2026-2027"].contracts == 1
    assert years["2026-2027"].gross_fee == 500.0


def test_status_breakdown_uses_derived_status(db: Session):
    stock = _stock(db)
    today = datetime.date(2026, 10, 30)
    ContractService.create_contract(  # expiring soon (4d)
        db=db, stock_id=stock.id, expiry_date=datetime.date(2026, 11, 3),
        quantity_lent=10, lending_fee=5.0, trade_date=datetime.date(2026, 10, 1),
    )
    ContractService.create_contract(  # active far future
        db=db, stock_id=stock.id, expiry_date=datetime.date(2027, 3, 2),
        quantity_lent=10, lending_fee=5.0, trade_date=datetime.date(2026, 10, 1),
    )
    breakdown = AnalyticsService.status_breakdown(db, today=today)
    assert breakdown.get("EXPIRING_SOON") == 1
    assert breakdown.get("ACTIVE") == 1


def test_yield_by_symbol_from_snapshots_only(db: Session):
    base = datetime.datetime(2026, 10, 1, 9, 0, 0)
    db.add_all([
        SLBSnapshot(series_family="B", symbol="RELIANCE", series="Oct-2026", expiry_date=datetime.date(2026, 10, 6),
                    annualized_yield=8.0, timestamp=base),
        SLBSnapshot(series_family="B", symbol="RELIANCE", series="Nov-2026", expiry_date=datetime.date(2026, 11, 3),
                    annualized_yield=10.0, timestamp=base + datetime.timedelta(days=1)),
    ])
    db.commit()
    ys = AnalyticsService.yield_by_symbol(db)
    assert len(ys) == 1
    rel = ys[0]
    assert rel.symbol == "RELIANCE"
    assert rel.latest_yield == 10.0  # most recent
    assert rel.average_yield == 9.0  # (8 + 10) / 2
    assert rel.samples == 2


def test_empty_analytics_is_safe(db: Session):
    summary = AnalyticsService.build_summary(db)
    assert summary.income_by_financial_year == []
    assert summary.yield_by_symbol == []
    assert summary.status_breakdown == {}
