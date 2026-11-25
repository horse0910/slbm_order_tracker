"""API tests for the alerts endpoints (rule CRUD + evaluate/deliver)."""

from __future__ import annotations

import pytest

import datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from slbm_tracker.db.models import SLBSnapshot

pytestmark = pytest.mark.usefixtures("pinned_snapshot_today")


def _snapshot(db: Session, annualized_yield: float) -> None:
    db.add(
        SLBSnapshot(series_family="B", 
            symbol="RELIANCE",
            series="Nov-2026",
            expiry_date=datetime.date(2026, 11, 3),
            annualized_yield=annualized_yield,
            timestamp=datetime.datetime.utcnow(),
        )
    )
    db.commit()


def test_create_list_and_delete_alert(client: TestClient):
    res = client.post(
        "/api/alerts", json={"alert_type": "YIELD_THRESHOLD", "threshold": 8.0}
    )
    assert res.status_code == 201, res.text
    alert_id = res.json()["id"]

    res_list = client.get("/api/alerts")
    assert res_list.status_code == 200
    assert len(res_list.json()) == 1

    res_del = client.delete(f"/api/alerts/{alert_id}")
    assert res_del.status_code == 204
    assert client.get("/api/alerts").json() == []


def test_create_alert_rejects_unknown_type(client: TestClient):
    res = client.post("/api/alerts", json={"alert_type": "BOGUS"})
    assert res.status_code == 422
    assert "Unknown alert_type" in res.json()["detail"]


def test_toggle_active(client: TestClient):
    alert_id = client.post(
        "/api/alerts", json={"alert_type": "ROLLOVER"}
    ).json()["id"]
    res = client.patch(f"/api/alerts/{alert_id}", json={"is_active": False})
    assert res.status_code == 200
    assert res.json()["is_active"] is False


def test_evaluate_fires_and_reports_delivery(client: TestClient, db: Session):
    _snapshot(db, 9.0)
    client.post("/api/alerts", json={"alert_type": "YIELD_THRESHOLD", "threshold": 8.0})

    res = client.post("/api/alerts/evaluate")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["evaluated"] == 1
    assert body["fired"] == 1
    assert body["results"][0]["condition_met"] is True
    # In-app delivery reported; email seam reported as not delivered.
    channels = {d["channel"]: d["delivered"] for d in body["deliveries"]}
    assert channels.get("in_app") is True
    assert channels.get("email") is False


def test_evaluate_is_idempotent_dedup(client: TestClient, db: Session):
    _snapshot(db, 9.0)
    client.post("/api/alerts", json={"alert_type": "YIELD_THRESHOLD", "threshold": 8.0})
    first = client.post("/api/alerts/evaluate").json()
    second = client.post("/api/alerts/evaluate").json()
    assert first["fired"] == 1
    assert second["fired"] == 0  # dedup: already triggered, not re-fired


def test_delete_missing_alert_404(client: TestClient):
    assert client.delete("/api/alerts/999").status_code == 404
