"""API tests for the contracts endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from slbm_tracker.db.models import Stock


def _add_stock(db: Session, symbol: str = "RELIANCE") -> int:
    stock = Stock(symbol=symbol, quantity=1000, avg_buy_price=2500.0)
    db.add(stock)
    db.commit()
    db.refresh(stock)
    return stock.id


def test_create_and_list_contract(client: TestClient, db: Session):
    stock_id = _add_stock(db)
    payload = {
        "stock_id": stock_id,
        "expiry_date": "2099-12-01",
        "quantity_lent": 100,
        "lending_fee": 5.5,
        "series": "Dec-2099",
        "trade_date": "2099-11-01",
    }
    res = client.post("/api/contracts", json=payload)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["symbol"] == "RELIANCE"
    assert body["quantity_lent"] == 100
    assert body["lending_fee_income"] == 550.0  # 100 * 5.5
    assert body["effective_status"] == "ACTIVE"  # far future
    assert body["is_rollover_candidate"] is False

    res_list = client.get("/api/contracts")
    assert res_list.status_code == 200
    assert len(res_list.json()) == 1


def test_create_contract_unknown_stock_returns_422(client: TestClient):
    payload = {
        "stock_id": 999999,
        "expiry_date": "2099-12-01",
        "quantity_lent": 100,
        "lending_fee": 5.5,
    }
    res = client.post("/api/contracts", json=payload)
    assert res.status_code == 422
    assert "not found" in res.json()["detail"]


def test_create_contract_validation_rejects_nonpositive_qty(client: TestClient, db: Session):
    stock_id = _add_stock(db)
    payload = {
        "stock_id": stock_id,
        "expiry_date": "2099-12-01",
        "quantity_lent": 0,  # violates gt=0 at schema layer
        "lending_fee": 5.5,
    }
    res = client.post("/api/contracts", json=payload)
    assert res.status_code == 422


def test_get_contract_404(client: TestClient):
    res = client.get("/api/contracts/4242")
    assert res.status_code == 404


def test_list_filter_by_effective_status(client: TestClient, db: Session):
    stock_id = _add_stock(db)
    # Far-future ACTIVE contract.
    client.post(
        "/api/contracts",
        json={
            "stock_id": stock_id,
            "expiry_date": "2099-12-01",
            "quantity_lent": 10,
            "lending_fee": 5.0,
            "trade_date": "2099-11-01",
        },
    )
    res = client.get("/api/contracts", params={"effective_status": "ACTIVE"})
    assert res.status_code == 200
    assert len(res.json()) == 1
    res_expired = client.get("/api/contracts", params={"effective_status": "EXPIRED"})
    assert res_expired.status_code == 200
    assert res_expired.json() == []


def test_create_contract_beyond_shares_in_demat_is_422(client: TestClient, db: Session):
    stock_id = _add_stock(db)  # 1000 shares, never imported
    payload = {
        "stock_id": stock_id,
        "expiry_date": "2099-12-01",
        "quantity_lent": 1001,
        "lending_fee": 1.0,
        "series": "XD",
        "trade_date": "2099-11-01",
    }
    res = client.post("/api/contracts", json=payload)
    assert res.status_code == 422
    assert "in demat" in res.json()["detail"]


def test_create_response_carries_warning_for_contract_traded_before_import(client: TestClient, db: Session):
    import datetime

    stock = Stock(symbol="SBIN", quantity=0, holdings_imported_at=datetime.datetime(2099, 11, 3, 4, 0))
    db.add(stock)
    db.commit()
    payload = {"stock_id": stock.id, "expiry_date": "2099-12-01", "quantity_lent": 500,
               "lending_fee": 1.0, "series": "XD", "trade_date": "2099-11-02"}
    res = client.post("/api/contracts", json=payload)
    assert res.status_code == 201, res.text
    assert res.json()["warning"].startswith("Traded before your last holdings import (03 Nov).")

    payload["trade_date"] = "2099-11-03"
    assert client.post("/api/contracts", json=payload).status_code == 422


def test_create_response_warning_is_null_on_the_strict_path(client: TestClient, db: Session):
    stock_id = _add_stock(db)
    payload = {"stock_id": stock_id, "expiry_date": "2099-12-01", "quantity_lent": 10,
               "lending_fee": 1.0, "series": "XD", "trade_date": "2099-11-01"}
    res = client.post("/api/contracts", json=payload)
    assert res.status_code == 201 and res.json()["warning"] is None
