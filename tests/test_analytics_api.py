"""API test for the analytics summary endpoint."""

from __future__ import annotations

import datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from slbm_tracker.db.models import Stock
from slbm_tracker.services.contract_service import ContractService
from slbm_tracker.services.pnl_service import PnLService


def test_analytics_summary_shape(client: TestClient, db: Session):
    stock = Stock(symbol="RELIANCE", quantity=1000, avg_buy_price=2500.0)
    db.add(stock)
    db.commit()
    db.refresh(stock)
    c = ContractService.create_contract(
        db=db, stock_id=stock.id, expiry_date=datetime.date(2026, 11, 3),
        quantity_lent=100, lending_fee=5.0, trade_date=datetime.date(2026, 10, 1),
    )
    PnLService.record_for_contract(db=db, contract_id=c.id)

    res = client.get("/api/analytics/summary")
    assert res.status_code == 200, res.text
    body = res.json()
    assert "status_breakdown" in body
    assert "income_by_financial_year" in body
    assert "yield_by_symbol" in body
    assert any(fy["financial_year"] == "2026-2027" for fy in body["income_by_financial_year"])


def test_analytics_summary_empty(client: TestClient):
    res = client.get("/api/analytics/summary")
    assert res.status_code == 200
    body = res.json()
    assert body["income_by_financial_year"] == []
    assert body["yield_by_symbol"] == []
