"""Tests for the Phase 5A delivery-audit API: GET /api/alerts/deliveries.

Covers the empty log, that an evaluate run populates the log with one row per
channel, the ``email_channel_active`` non-secret flag, limit validation, and the
naive-UTC timestamp convention shared with the rest of the API.
"""

from __future__ import annotations

import pytest

import datetime
from unittest import mock

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from slbm_tracker.config import Settings
from slbm_tracker.db.models import SLBSnapshot
from slbm_tracker.services.alert_service import ALERT_YIELD_THRESHOLD, AlertService

pytestmark = pytest.mark.usefixtures("pinned_snapshot_today")


def _configured_settings(**overrides) -> Settings:
    base = dict(
        notifications_enabled=True,
        smtp_host="smtp.example.com",
        alert_email_to="recipient@example.com",
    )
    base.update(overrides)
    return Settings(**base)


def _seed_fireable_alert(db: Session) -> None:
    """Seed a snapshot + a portfolio-wide rule that will fire on evaluate."""
    db.add(
        SLBSnapshot(series_family="B", 
            symbol="RELIANCE",
            series="Nov-2026",
            expiry_date=datetime.date(2026, 11, 3),
            annualized_yield=9.0,
            timestamp=datetime.datetime.utcnow(),
        )
    )
    db.commit()
    AlertService.create_alert(db=db, alert_type=ALERT_YIELD_THRESHOLD, threshold=8.0)


def test_deliveries_empty_log(client: TestClient):
    resp = client.get("/api/alerts/deliveries")
    assert resp.status_code == 200
    data = resp.json()
    assert data["email_channel_active"] is False  # not configured in test env
    assert data["deliveries"] == []


def test_deliveries_populated_after_evaluate(client: TestClient, db: Session):
    _seed_fireable_alert(db)

    ev = client.post("/api/alerts/evaluate")
    assert ev.status_code == 200
    assert ev.json()["fired"] == 1

    resp = client.get("/api/alerts/deliveries")
    assert resp.status_code == 200
    data = resp.json()
    deliveries = data["deliveries"]
    # One row per channel for the single fired alert.
    assert {row["channel"] for row in deliveries} == {"in_app", "email", "macos", "whatsapp"}
    for row in deliveries:
        assert set(row) >= {"id", "alert_id", "channel", "delivered", "detail", "outcome", "created_at"}
        assert row["created_at"]
        assert "Z" not in row["created_at"]  # naive-UTC convention (REST↔WS contract)

    in_app = next(r for r in deliveries if r["channel"] == "in_app")
    assert in_app["delivered"] is True
    email = next(r for r in deliveries if r["channel"] == "email")
    assert email["delivered"] is False  # email channel off in test env


def test_deliveries_limit_validation_and_cap(client: TestClient, db: Session):
    _seed_fireable_alert(db)
    client.post("/api/alerts/evaluate")  # writes 2 rows (in_app + email)

    assert client.get("/api/alerts/deliveries?limit=0").status_code == 422
    assert client.get("/api/alerts/deliveries?limit=501").status_code == 422

    r = client.get("/api/alerts/deliveries?limit=1")
    assert r.status_code == 200
    assert len(r.json()["deliveries"]) == 1  # capped to the requested limit


def test_deliveries_reports_email_channel_active(client: TestClient):
    with mock.patch(
        "slbm_tracker.services.alert_delivery_service.get_settings",
        return_value=_configured_settings(),
    ):
        resp = client.get("/api/alerts/deliveries")
    assert resp.status_code == 200
    assert resp.json()["email_channel_active"] is True
