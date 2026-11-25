"""Unit tests for Pydantic request and response schemas."""

import datetime
import pytest
from pydantic import ValidationError

from slbm_tracker.api.schemas import (
    HealthResponse,
    PortfolioImportResponse,
    SLBQuoteItem,
    StockCreate,
    StockResponse,
    StockUpdate,
)


def test_stock_create_valid():
    data = {"symbol": "RELIANCE", "quantity": 100, "avg_buy_price": 2500.0}
    schema = StockCreate(**data)
    assert schema.symbol == "RELIANCE"
    assert schema.quantity == 100
    assert schema.is_slb_eligible is True


def test_stock_create_invalid_quantity():
    with pytest.raises(ValidationError):
        StockCreate(symbol="TCS", quantity=-1)


def test_stock_create_invalid_price():
    with pytest.raises(ValidationError):
        StockCreate(symbol="TCS", quantity=10, avg_buy_price=-50.0)


def test_stock_update_partial():
    update = StockUpdate(quantity=50)
    assert update.quantity == 50
    assert update.avg_buy_price is None


def test_slb_quote_item_schema():
    now = datetime.datetime.utcnow()
    quote = SLBQuoteItem(
        symbol="RELIANCE",
        series="Oct-2026",
        expiry_date=datetime.date(2026, 10, 6),
        best_bid=4.5,
        best_bid_qty=500,
        best_ask=5.25,
        best_ask_qty=1000,
        ltp=5.0,
        annualized_yield=8.55,
        timestamp=now,
    )
    assert quote.symbol == "RELIANCE"
    assert quote.annualized_yield == 8.55


def test_health_response_schema():
    now = datetime.datetime.utcnow()
    res = HealthResponse(status="healthy", version="0.1.0", database="ok", timestamp=now)
    assert res.status == "healthy"
    assert res.database == "ok"
