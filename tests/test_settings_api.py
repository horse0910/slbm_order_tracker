"""API tests for the settings endpoint: read + Phase 5D edit/reset.

Verifies the non-secret contract (GET/PUT/DELETE expose and accept only the
whitelisted business prefs, never secrets), that a PUT persists and flows
through the same resolver cache the engines read, all-or-nothing validation
(422 persists nothing), partial updates, and reset-to-env.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from slbm_tracker.config import get_settings
from slbm_tracker.db.models import AppSetting
from slbm_tracker.services import business_config

# Secret/credential-looking substrings that must never appear as a response key.
_FORBIDDEN = ("password", "secret", "token", "smtp", "api_key", "apikey", "credential")


def test_settings_exposes_business_config(client: TestClient):
    res = client.get("/api/settings")
    assert res.status_code == 200, res.text
    body = res.json()
    # Business preferences that drive the engines are present.
    for key in (
        "expiring_soon_days",
        "default_alert_yield_threshold",
        "tight_spread_threshold_pct",
        "above_average_yield_margin_pct",
        "notifications_enabled",
        "broker_processing_fee_pct",
        "gst_pct",
        "dp_charge_flat_inr",
        "default_tax_slab_pct",
    ):
        assert key in body
    assert "dp_charge_pct" not in body
    assert body["dp_charge_flat_inr"] == 13.0


def test_settings_exposes_no_secrets(client: TestClient):
    body = client.get("/api/settings").json()
    # No credential/secret-looking keys are ever exposed to the frontend.
    lowered = {k.lower() for k in body}
    for forbidden in ("password", "secret", "token", "smtp", "api_key", "apikey", "credential"):
        assert not any(forbidden in k for k in lowered)


def test_put_persists_get_reflects_and_flows_to_resolver(client: TestClient, db: Session):
    res = client.put("/api/settings", json={"gst_pct": 11.0, "expiring_soon_days": 21})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["gst_pct"] == 11.0 and body["expiring_soon_days"] == 21
    # Persisted to app_settings, one row per changed key.
    assert {r.key for r in db.query(AppSetting).all()} == {"gst_pct", "expiring_soon_days"}
    # A fresh GET reloads from the DB and reflects the override...
    assert client.get("/api/settings").json()["gst_pct"] == 11.0
    # ...and it flows through the SAME resolver cache the engines read.
    assert business_config.effective("gst_pct") == 11.0
    assert business_config.effective("expiring_soon_days") == 21


def test_put_out_of_range_returns_422_and_persists_nothing(client: TestClient, db: Session):
    # Pass schema typing but violate the central business_config bounds.
    assert client.put("/api/settings", json={"expiring_soon_days": 0}).status_code == 422
    assert client.put("/api/settings", json={"gst_pct": 150.0}).status_code == 422
    assert db.query(AppSetting).count() == 0
    assert client.get("/api/settings").json()["gst_pct"] == get_settings().gst_pct


def test_put_rejects_secret_and_unknown_keys(client: TestClient, db: Session):
    # extra="forbid" rejects SMTP secrets, other env-only fields, and typos.
    for payload in ({"smtp_password": "x"}, {"smtp_host": "h"}, {"api_port": 9}, {"nope": 1}):
        assert client.put("/api/settings", json=payload).status_code == 422, payload
    assert db.query(AppSetting).count() == 0


def test_put_rejects_non_coercible_type(client: TestClient):
    assert client.put("/api/settings", json={"gst_pct": "not-a-number"}).status_code == 422

def test_partial_put_leaves_other_fields_at_env_default(client: TestClient):
    body = client.put("/api/settings", json={"gst_pct": 9.0}).json()
    s = get_settings()
    assert body["gst_pct"] == 9.0
    # Only the sent field changes; everything else stays at the env base.
    assert body["expiring_soon_days"] == s.expiring_soon_days
    assert body["default_tax_slab_pct"] == s.default_tax_slab_pct
    assert body["notifications_enabled"] == s.notifications_enabled


def test_delete_resets_all_overrides_to_env(client: TestClient, db: Session):
    client.put("/api/settings", json={"gst_pct": 3.0, "notifications_enabled": False})
    res = client.delete("/api/settings")
    assert res.status_code == 200, res.text
    s = get_settings()
    assert res.json()["gst_pct"] == s.gst_pct
    assert res.json()["notifications_enabled"] == s.notifications_enabled
    assert db.query(AppSetting).count() == 0
    assert client.get("/api/settings").json()["gst_pct"] == s.gst_pct


def test_mutation_responses_never_expose_secrets(client: TestClient):
    # The write paths uphold the same no-secrets invariant as GET.
    put_body = client.put("/api/settings", json={"gst_pct": 10.0}).json()
    del_body = client.delete("/api/settings").json()
    for body in (put_body, del_body):
        lowered = {k.lower() for k in body}
        for forbidden in _FORBIDDEN:
            assert not any(forbidden in k for k in lowered)


def test_put_notifications_enabled_is_editable(client: TestClient):
    body = client.put("/api/settings", json={"notifications_enabled": False}).json()
    assert body["notifications_enabled"] is False
    assert business_config.effective("notifications_enabled") is False


def test_get_cold_cache_reloads_persisted_override(client: TestClient, db: Session):
    # Simulate a fresh process: an override is persisted but the in-process cache
    # is empty (e.g. startup refresh hasn't run). GET must reload from the DB via
    # its own refresh(db), not silently serve env defaults.
    db.add(AppSetting(key="gst_pct", value=json.dumps(7.5)))
    db.commit()
    business_config.reset_cache()
    assert business_config.effective("gst_pct") == get_settings().gst_pct  # cache is cold
    body = client.get("/api/settings").json()
    assert body["gst_pct"] == 7.5  # GET reloaded the persisted override
    assert business_config.effective("gst_pct") == 7.5  # and warmed the cache


def test_dp_charge_flat_is_editable_and_old_percent_key_is_rejected(client: TestClient):
    res = client.put("/api/settings", json={"dp_charge_flat_inr": 15.5})
    assert res.status_code == 200, res.text
    assert res.json()["dp_charge_flat_inr"] == 15.5
    assert client.put("/api/settings", json={"dp_charge_pct": 0.02}).status_code == 422


def test_live_refresh_seconds_is_editable_within_bounds(client: TestClient):
    assert client.get("/api/settings").json()["live_refresh_seconds"] == 180
    res = client.put("/api/settings", json={"live_refresh_seconds": 300})
    assert res.status_code == 200 and res.json()["live_refresh_seconds"] == 300
    assert client.put("/api/settings", json={"live_refresh_seconds": 10}).status_code == 422


def test_opportunity_settings_are_editable_with_bounds(client: TestClient):
    body = client.get("/api/settings").json()
    assert (body["min_net_yield_pct"], body["min_fill_qty"], body["tracked_expiries"]) == (4.0, 1, 3)
    res = client.put("/api/settings", json={"min_net_yield_pct": 6.5, "min_fill_qty": 100, "tracked_expiries": 6})
    assert res.status_code == 200, res.text
    assert (res.json()["min_net_yield_pct"], res.json()["min_fill_qty"], res.json()["tracked_expiries"]) == (6.5, 100, 6)
    assert client.put("/api/settings", json={"tracked_expiries": 7}).status_code == 422
    assert client.put("/api/settings", json={"tracked_expiries": 0}).status_code == 422
    assert client.put("/api/settings", json={"min_fill_qty": 0}).status_code == 422
    assert client.put("/api/settings", json={"series_family": "A"}).status_code == 422  # env-only
