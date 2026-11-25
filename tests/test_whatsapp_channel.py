"""Phase 5.3: WhatsApp via Twilio (HTTP mocked), only for LENDING_OPPORTUNITY
and ROLLOVER; on only when all four env settings are set; the credentials are
never stored, logged or returned."""

from __future__ import annotations

import logging

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from slbm_tracker.config import get_settings
from slbm_tracker.db.models import Alert, NotificationDelivery
from slbm_tracker.services import notification_channels as nc
from slbm_tracker.services.alert_delivery_service import AlertDeliveryService

SID, TOKEN = "AC_test_sid_123", "tok_secret_456"


@pytest.fixture()
def configured(monkeypatch):
    s = get_settings()
    for name, value in (("twilio_account_sid", SID), ("twilio_auth_token", TOKEN),
                        ("whatsapp_from", "+14155238886"), ("whatsapp_to", "+919800000000")):
        monkeypatch.setattr(s, name, value)
    sent: list[dict] = []

    def fake_post(url, *, auth, data, timeout):
        sent.append({"url": url, "auth": auth, "data": data})
        return httpx.Response(201, json={"sid": "SM1"})

    monkeypatch.setattr(nc, "_http_post", fake_post)
    return sent


def _alert(db: Session, alert_type: str) -> Alert:
    a = Alert(alert_type=alert_type, is_active=True, is_triggered=True, trigger_message="IRFC XD nets 6.82% p.a.")
    db.add(a)
    db.commit()
    return a


def _whatsapp(db: Session, alert: Alert):
    return next(r for r in AlertDeliveryService.deliver(db, [alert.id]) if r.channel == "whatsapp")


def test_not_configured_is_skipped(db: Session):
    r = _whatsapp(db, _alert(db, "LENDING_OPPORTUNITY"))
    assert (r.delivered, r.outcome) == (False, "skipped") and "not configured" in r.detail


@pytest.mark.parametrize("alert_type", ["LENDING_OPPORTUNITY", "ROLLOVER"])
def test_sent_for_opportunity_and_rollover_alerts(db: Session, configured, alert_type):
    r = _whatsapp(db, _alert(db, alert_type))
    assert (r.delivered, r.outcome) == (True, "sent")
    call = configured[-1]
    assert call["url"] == f"https://api.twilio.com/2010-04-01/Accounts/{SID}/Messages.json"
    assert call["auth"] == (SID, TOKEN)
    assert call["data"]["From"] == "whatsapp:+14155238886" and call["data"]["To"] == "whatsapp:+919800000000"
    assert "IRFC XD nets 6.82% p.a." in call["data"]["Body"]
    row = db.query(NotificationDelivery).filter_by(channel="whatsapp").one()
    assert (row.outcome, row.delivered) == ("sent", True)


def test_other_alert_types_are_not_sent(db: Session, configured):
    r = _whatsapp(db, _alert(db, "YIELD_THRESHOLD"))
    assert r.outcome == "skipped" and "only for" in r.detail
    assert configured == []


def test_failures_never_leak_credentials(db: Session, configured, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)

    def fail(url, **kw):
        raise httpx.ConnectError(f"cannot reach {url} as {kw['auth']}")

    monkeypatch.setattr(nc, "_http_post", fail)
    r = _whatsapp(db, _alert(db, "ROLLOVER"))
    assert r.outcome == "failed" and r.detail == "WhatsApp send failed: ConnectError"
    monkeypatch.setattr(nc, "_http_post", lambda url, **kw: httpx.Response(401, text=f"bad {SID}"))
    r2 = _whatsapp(db, _alert(db, "ROLLOVER"))
    assert r2.detail == "WhatsApp send failed: HTTP 401"
    stored = " ".join(row.detail or "" for row in db.query(NotificationDelivery).all())
    for secret in (SID, TOKEN):
        assert secret not in stored
        assert secret not in caplog.text


def test_api_never_returns_credentials(client: TestClient, configured):
    for path in ("/api/settings", "/api/alerts/deliveries", "/api/notifications/status"):
        text = client.get(path).text
        assert SID not in text and TOKEN not in text
    status = client.get("/api/notifications/status").json()
    assert status["whatsapp_configured"] is True and set(status) >= {"macos_available", "macos_enabled"}
