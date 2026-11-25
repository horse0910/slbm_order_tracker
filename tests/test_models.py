"""Unit tests for SQLAlchemy database models."""

import datetime
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from slbm_tracker.db.models import Alert, LendingContract, PnLRecord, SLBSnapshot, Stock


def test_create_stock(db: Session):
    stock = Stock(
        symbol="RELIANCE",
        name="Reliance Industries Limited",
        isin="INE002A01018",
        quantity=100,
        avg_buy_price=2500.0,
        is_slb_eligible=True,
    )
    db.add(stock)
    db.commit()

    retrieved = db.query(Stock).filter(Stock.symbol == "RELIANCE").first()
    assert retrieved is not None
    assert retrieved.id is not None
    assert retrieved.symbol == "RELIANCE"
    assert retrieved.quantity == 100
    assert retrieved.avg_buy_price == 2500.0
    assert retrieved.is_slb_eligible is True
    assert retrieved.created_at is not None


def test_stock_unique_symbol_constraint(db: Session):
    s1 = Stock(symbol="TCS", quantity=10, avg_buy_price=3500.0)
    db.add(s1)
    db.commit()

    s2 = Stock(symbol="TCS", quantity=20, avg_buy_price=3600.0)
    db.add(s2)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_lending_contract_and_pnl_relationship(db: Session):
    stock = Stock(symbol="INFY", quantity=200, avg_buy_price=1400.0)
    db.add(stock)
    db.commit()

    contract = LendingContract(
        stock_id=stock.id,
        series="Oct-2026",
        expiry_date=datetime.date(2026, 10, 6),
        quantity_lent=50,
        lending_fee=12.50,
        status="ACTIVE",
    )
    db.add(contract)
    db.commit()

    assert contract.id is not None
    assert contract.stock.symbol == "INFY"

    # Add PnL record
    pnl = PnLRecord(
        contract_id=contract.id,
        gross_fee=625.0,  # 50 * 12.50
        processing_charge=125.0,  # 20%
        gst=22.50,  # 18% of 125
        dp_charge=14.0,
        net_income=463.50,
        tax_slab_pct=30.0,
        estimated_tax=139.05,
        post_tax_income=324.45,
    )
    db.add(pnl)
    db.commit()

    assert contract.pnl_record is not None
    assert contract.pnl_record.net_income == 463.50
    assert pnl.contract.series == "Oct-2026"


def test_cascade_delete_stock(db: Session):
    stock = Stock(symbol="SBIN", quantity=150, avg_buy_price=600.0)
    db.add(stock)
    db.commit()

    contract = LendingContract(
        stock_id=stock.id,
        series="Nov-2026",
        expiry_date=datetime.date(2026, 11, 3),
        quantity_lent=50,
        lending_fee=3.0,
        status="ACTIVE",
    )
    alert = Alert(
        stock_id=stock.id,
        alert_type="YIELD_THRESHOLD",
        threshold=10.0,
        is_active=True,
    )
    db.add_all([contract, alert])
    db.commit()

    contract_id = contract.id
    alert_id = alert.id

    # Deleting stock must cascade to contract and alert
    db.delete(stock)
    db.commit()

    assert db.query(Stock).filter(Stock.id == stock.id).first() is None
    assert db.query(LendingContract).filter(LendingContract.id == contract_id).first() is None
    assert db.query(Alert).filter(Alert.id == alert_id).first() is None


def test_slb_snapshot_model(db: Session):
    snapshot = SLBSnapshot(
        symbol="TATASTEEL",
        series="Oct-2026",
        expiry_date=datetime.date(2026, 10, 6),
        best_bid=1.20,
        best_ask=1.45,
        ltp=1.35,
        volume=10000,
        open_positions=45000,
        annualized_yield=9.2,
        underlying_price=145.0,
        source="NSE_API",
    )
    db.add(snapshot)
    db.commit()

    retrieved = db.query(SLBSnapshot).filter(SLBSnapshot.symbol == "TATASTEEL").first()
    assert retrieved is not None
    assert retrieved.ltp == 1.35
    assert retrieved.annualized_yield == 9.2
    assert retrieved.source == "NSE_API"
