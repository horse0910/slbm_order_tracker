"""Phase 4.3: POST /api/contracts/{id}/close, /recall, /roll and PATCH."""

from __future__ import annotations

import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from slbm_tracker.db.models import PnLRecord, Stock


@pytest.fixture()
def contract_id(client: TestClient, db: Session) -> int:
    stock = Stock(symbol="SBIN", quantity=500)
    db.add(stock)
    db.commit()
    res = client.post("/api/contracts", json={
        "stock_id": stock.id, "expiry_date": "2099-11-03", "reverse_leg_date": "2099-11-03",
        "quantity_lent": 300, "lending_fee": 1.0, "series": "Nov-2099", "series_code": "XN",
        "trade_date": "2099-10-01",
    })
    assert res.status_code == 201, res.text
    body = res.json()
    assert (body["origin_date"], body["series_code"], body["rolled_from_id"]) == ("2099-10-01", "XN", None)
    return body["id"]


def test_close_twice_books_pnl_once(client: TestClient, db: Session, contract_id: int):
    first = client.post(f"/api/contracts/{contract_id}/close")
    assert first.status_code == 200, first.text
    assert first.json()["stored_status"] == "CLOSED" and first.json()["closed_on"]
    assert client.post(f"/api/contracts/{contract_id}/close").status_code == 200
    assert db.query(PnLRecord).count() == 1


def test_recall_then_close_is_refused(client: TestClient, contract_id: int):
    assert client.post(f"/api/contracts/{contract_id}/recall").json()["stored_status"] == "RECALLED"
    res = client.post(f"/api/contracts/{contract_id}/close")
    assert res.status_code == 409 and "RECALLED" in res.json()["detail"]


def test_roll_returns_the_new_contract(client: TestClient, contract_id: int):
    res = client.post(f"/api/contracts/{contract_id}/roll", json={
        "expiry_date": "2099-12-01", "reverse_leg_date": "2099-12-01", "lending_fee": 1.25,
        "series": "Dec-2099", "series_code": "XD", "trade_date": "2099-11-02",
    })
    assert res.status_code == 201, res.text
    new = res.json()
    assert (new["rolled_from_id"], new["origin_date"], new["series_code"]) == (contract_id, "2099-10-01", "XD")
    assert new["chain_days"] == 61  # settlement 1 Dec - origin 1 Oct
    old = client.get(f"/api/contracts/{contract_id}").json()
    assert old["stored_status"] == "ROLLED_OVER"


def test_roll_past_the_365_day_cap_is_422(client: TestClient, contract_id: int):
    res = client.post(f"/api/contracts/{contract_id}/roll", json={
        "expiry_date": "2100-11-06", "lending_fee": 1.0, "trade_date": "2099-11-02",
    })
    assert res.status_code == 422 and "365" in res.json()["detail"]


def test_patch_corrects_fields_and_rebooks_a_closed_contracts_pnl(client: TestClient, db: Session, contract_id: int):
    res = client.patch(f"/api/contracts/{contract_id}", json={"notes": "fee per ticket", "lending_fee": 1.5})
    assert res.status_code == 200 and res.json()["lending_fee"] == 1.5 and res.json()["notes"] == "fee per ticket"
    client.post(f"/api/contracts/{contract_id}/close")
    client.patch(f"/api/contracts/{contract_id}", json={"lending_fee": 2.0})
    assert db.query(PnLRecord).one().gross_fee == 600.0  # re-booked, still one record


def test_patch_rejects_status_and_bad_dates(client: TestClient, contract_id: int):
    assert client.patch(f"/api/contracts/{contract_id}", json={"status": "CLOSED"}).status_code == 422
    assert client.patch(f"/api/contracts/{contract_id}", json={"expiry_date": "2099-09-01"}).status_code == 422


def test_unknown_contract_is_404(client: TestClient):
    for path in ("close", "recall"):
        assert client.post(f"/api/contracts/9999/{path}").status_code == 404
    assert client.patch("/api/contracts/9999", json={"notes": "x"}).status_code == 404


def test_roll_options_for_any_open_contract(client: TestClient, db: Session, contract_id: int):
    from slbm_tracker.db.models import SLBSnapshot

    db.add(SLBSnapshot(symbol="SBIN", series="Dec-2099", series_code="XD", series_family="B",
                       expiry_date=datetime.date(2099, 12, 1), settlement_date=datetime.date(2099, 12, 1),
                       best_bid=1.3, best_bid_qty=1000, ltp=1.3, underlying_price=800.0,
                       timestamp=datetime.datetime(2099, 10, 2, 6, 0), source="NSE_QUOTE"))
    db.commit()
    res = client.get(f"/api/contracts/{contract_id}/roll-options")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "ROLL_AVAILABLE" and body["best_option"]["series_code"] == "XD"
    assert body["best_option"]["best_bid"] == 1.3 and body["best_option"]["within_cap"] is True
    assert body["days_used_label"].endswith("of 365 days used")
    assert client.get("/api/contracts/9999/roll-options").status_code == 404
    client.post(f"/api/contracts/{contract_id}/close")
    assert client.get(f"/api/contracts/{contract_id}/roll-options").status_code == 409
