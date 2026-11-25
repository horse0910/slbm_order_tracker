"""Dashboard integration: contract counts use the DERIVED lifecycle status so
the dashboard cannot drift from the Contracts page, and income roll-ups reflect
persisted P&L.
"""

from __future__ import annotations

import datetime

from sqlalchemy.orm import Session

from slbm_tracker.db.models import Stock
from slbm_tracker.services.contract_service import ContractService
from slbm_tracker.services.dashboard_service import DashboardService
from slbm_tracker.services.pnl_service import PnLService


def _stock(db: Session) -> Stock:
    stock = Stock(symbol="RELIANCE", quantity=1000, avg_buy_price=2500.0)
    db.add(stock)
    db.commit()
    db.refresh(stock)
    return stock


def test_dashboard_counts_use_derived_status(db: Session):
    stock = _stock(db)
    today = datetime.date(2026, 10, 30)
    # ACTIVE (far future)
    ContractService.create_contract(
        db=db, stock_id=stock.id, expiry_date=datetime.date(2027, 3, 2),
        quantity_lent=10, lending_fee=5.0, trade_date=datetime.date(2026, 10, 1),
    )
    # EXPIRING_SOON (4d out)
    ContractService.create_contract(
        db=db, stock_id=stock.id, expiry_date=datetime.date(2026, 11, 3),
        quantity_lent=10, lending_fee=5.0, trade_date=datetime.date(2026, 10, 1),
    )
    # Past expiry but NOT reconciled — stored status still ACTIVE, yet the
    # dashboard must count it as EXPIRED via derivation.
    ContractService.create_contract(
        db=db, stock_id=stock.id, expiry_date=datetime.date(2026, 10, 1),
        quantity_lent=10, lending_fee=5.0, trade_date=datetime.date(2026, 9, 1),
    )

    summary = DashboardService.build_summary(db=db, today=today)
    assert summary["contracts"]["active"] == 1
    assert summary["contracts"]["expiring_soon"] == 1
    assert summary["contracts"]["expired"] == 1  # derived, despite stored ACTIVE


def test_dashboard_income_reflects_recorded_pnl(db: Session):
    stock = _stock(db)
    today = datetime.date(2026, 10, 30)
    c = ContractService.create_contract(
        db=db, stock_id=stock.id, expiry_date=datetime.date(2026, 11, 3),
        quantity_lent=100, lending_fee=5.0, trade_date=datetime.date(2026, 10, 1),
    )
    PnLService.record_for_contract(db=db, contract_id=c.id)
    summary = DashboardService.build_summary(db=db, today=today)
    assert summary["income"]["total_gross_fee"] == 500.0
    assert summary["income"]["total_net_income"] == 366.66
