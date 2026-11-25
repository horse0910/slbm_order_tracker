"""Unit tests for PortfolioService."""

import pytest
from sqlalchemy.orm import Session

from slbm_tracker.db.models import Stock
from slbm_tracker.services.portfolio_service import PortfolioService


def test_add_stock_success(db: Session):
    stock = PortfolioService.add_or_update_stock(
        db=db,
        symbol="infy",  # Should be normalized to INFY
        quantity=50,
        avg_buy_price=1500.25,
        name="Infosys Ltd",
        isin="INE009A01021",
    )
    assert stock.symbol == "INFY"
    assert stock.quantity == 50
    assert stock.avg_buy_price == 1500.25
    assert stock.name == "Infosys Ltd"


def test_add_existing_stock_updates_values(db: Session):
    s1 = PortfolioService.add_or_update_stock(db=db, symbol="TCS", quantity=20, avg_buy_price=3400.0)
    assert s1.quantity == 20

    s2 = PortfolioService.add_or_update_stock(db=db, symbol="TCS", quantity=35, avg_buy_price=3500.0)
    assert s2.id == s1.id
    assert s2.quantity == 35
    assert s2.avg_buy_price == 3500.0

    all_tcs = db.query(Stock).filter(Stock.symbol == "TCS").all()
    assert len(all_tcs) == 1


def test_symbol_validation_errors(db: Session):
    with pytest.raises(ValueError, match="Stock symbol cannot be empty"):
        PortfolioService.add_or_update_stock(db=db, symbol="", quantity=10)

    with pytest.raises(ValueError, match="Invalid symbol format"):
        PortfolioService.add_or_update_stock(db=db, symbol="INVALID!#$", quantity=10)

    with pytest.raises(ValueError, match="Quantity must be non-negative"):
        PortfolioService.add_or_update_stock(db=db, symbol="WIPRO", quantity=-5)

    with pytest.raises(ValueError, match="Average buy price must be non-negative"):
        PortfolioService.add_or_update_stock(db=db, symbol="WIPRO", quantity=10, avg_buy_price=-100.0)


def test_get_and_list_stocks(db: Session):
    PortfolioService.add_or_update_stock(db=db, symbol="RELIANCE", quantity=100, is_slb_eligible=True)
    PortfolioService.add_or_update_stock(db=db, symbol="IDEA", quantity=500, is_slb_eligible=False)

    all_stocks = PortfolioService.list_stocks(db=db)
    assert len(all_stocks) == 2

    eligible_only = PortfolioService.list_stocks(db=db, is_slb_eligible=True)
    assert len(eligible_only) == 1
    assert eligible_only[0].symbol == "RELIANCE"

    found = PortfolioService.get_stock_by_symbol(db=db, symbol="reliance")
    assert found is not None
    assert found.symbol == "RELIANCE"


def test_delete_stock(db: Session):
    stock = PortfolioService.add_or_update_stock(db=db, symbol="HDFC", quantity=10)
    assert PortfolioService.delete_stock(db=db, stock_id=stock.id) is True
    assert PortfolioService.get_stock(db=db, stock_id=stock.id) is None
    # Deleting non-existent returns False
    assert PortfolioService.delete_stock(db=db, stock_id=99999) is False
