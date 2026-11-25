"""API tests for the P&L endpoints (preview, record, list totals)."""

from __future__ import annotations

import datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from slbm_tracker.db.models import Stock
from slbm_tracker.services.contract_service import ContractService


def _contract(db: Session) -> int:
    stock = Stock(symbol="RELIANCE", quantity=1000, avg_buy_price=2500.0)
    db.add(stock)
    db.commit()
    db.refresh(stock)
    c = ContractService.create_contract(
        db=db, stock_id=stock.id, expiry_date=datetime.date(2026, 11, 3),
        quantity_lent=100, lending_fee=5.0, trade_date=datetime.date(2026, 10, 1),
    )
    return c.id


def test_preview_returns_itemized_breakdown(client: TestClient):
    res = client.post(
        "/api/pnl/preview",
        json={"quantity_lent": 100, "lending_fee": 5.0, "tax_slab_pct": 30.0},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["gross_fee"] == 500.0
    assert body["processing_charge"] == 100.0
    assert body["gst"] == 18.0
    assert body["net_income"] == 366.66  # 500 - 100 - 18 - 15.34 (flat DP)
    assert body["estimated_tax"] == 110.0
    assert body["post_tax_income"] == 256.66


def test_preview_rejects_negative(client: TestClient):
    res = client.post("/api/pnl/preview", json={"quantity_lent": -1, "lending_fee": 5.0})
    assert res.status_code == 422  # schema ge=0


def test_record_and_list_totals(client: TestClient, db: Session):
    contract_id = _contract(db)
    res = client.post(f"/api/pnl/contracts/{contract_id}")
    assert res.status_code == 200, res.text
    assert res.json()["symbol"] == "RELIANCE"
    assert res.json()["gross_fee"] == 500.0

    res_list = client.get("/api/pnl")
    assert res_list.status_code == 200
    body = res_list.json()
    assert body["totals"]["records"] == 1
    assert body["totals"]["gross_fee"] == 500.0
    assert body["totals"]["net_income"] == 366.66


def test_record_missing_contract_422(client: TestClient):
    assert client.post("/api/pnl/contracts/999").status_code == 422


def test_get_pnl_before_compute_404(client: TestClient, db: Session):
    contract_id = _contract(db)
    assert client.get(f"/api/pnl/contracts/{contract_id}").status_code == 404
