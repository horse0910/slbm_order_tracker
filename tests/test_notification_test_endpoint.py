"""Phase 5.5: POST /api/notifications/test fires the macOS channel, plus
WhatsApp when configured, and logs each attempt (no alert attached)."""

from __future__ import annotations

import httpx
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from slbm_tracker.config import get_settings
from slbm_tracker.db.models import NotificationDelivery
from slbm_tracker.services import notification_channels as nc


def test_sends_macos_and_skips_unconfigured_whatsapp(client: TestClient, db: Session, monkeypatch, _no_real_notifications):
    monkeypatch.setattr(nc, "macos_available", lambda: True)
    res = client.post("/api/notifications/test")
    assert res.status_code == 200, res.text
    by = {r["channel"]: r for r in res.json()["results"]}
    assert (by["macos"]["outcome"], by["whatsapp"]["outcome"]) == ("sent", "skipped")
    assert "SLBM Tracker test" in _no_real_notifications[-1][2]
    rows = db.query(NotificationDelivery).all()
    assert {r.channel for r in rows} == {"macos", "whatsapp"} and all(r.alert_id is None for r in rows)


def test_sends_whatsapp_when_configured(client: TestClient, monkeypatch):
    s = get_settings()
    for name, value in (("twilio_account_sid", "AC1"), ("twilio_auth_token", "t"),
                        ("whatsapp_from", "+1"), ("whatsapp_to", "+91")):
        monkeypatch.setattr(s, name, value)
    sent = []
    monkeypatch.setattr(nc, "_http_post", lambda url, **kw: sent.append(kw["data"]) or httpx.Response(201))
    by = {r["channel"]: r for r in client.post("/api/notifications/test").json()["results"]}
    assert by["whatsapp"]["outcome"] == "sent" and "test" in sent[0]["Body"].lower()


def test_pwa_files_exist_and_manifest_points_at_the_icons():
    import json
    from pathlib import Path

    public = Path(__file__).resolve().parent.parent / "frontend" / "public"
    manifest = json.loads((public / "manifest.json").read_text())
    assert manifest["display"] == "standalone" and manifest["start_url"] == "/"
    for icon in manifest["icons"]:
        png = (public / icon["src"].lstrip("/")).read_bytes()
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        w, h = int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")
        assert f"{w}x{h}" == icon["sizes"]
    sw = (public / "sw.js").read_text()
    assert "push" not in sw.lower().replace("no web push", "")  # no web push (decided 4 Oct)
