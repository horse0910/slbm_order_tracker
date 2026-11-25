"""Phase 5.1: macOS notifications via osascript (subprocess mocked).

Title and body are escaped as AppleScript string literals and passed as one
argv item (no shell). Not on macOS the channel is skipped, not failed. Every
attempt lands in the delivery log with its outcome.
"""

from __future__ import annotations

import subprocess

from sqlalchemy.orm import Session

from slbm_tracker.db.models import Alert, NotificationDelivery
from slbm_tracker.services import notification_channels as nc
from slbm_tracker.services.alert_delivery_service import AlertDeliveryService


def test_applescript_quoting_escapes_backslashes_quotes_and_newlines():
    assert nc.applescript_quote('He said "hi" \\ bye\nnext') == '"He said \\"hi\\" \\\\ bye next"'


def test_send_macos_runs_osascript_with_an_escaped_script(monkeypatch, _no_real_notifications):
    monkeypatch.setattr(nc, "macos_available", lambda: True)
    ok, outcome, detail = nc.send_macos('IRFC "XD"', "nets 6.82%\\day")
    assert (ok, outcome) == (True, "sent")
    args = _no_real_notifications[-1]
    assert args[:2] == ["osascript", "-e"] and len(args) == 3
    assert args[2] == (
        'display notification "nets 6.82%\\\\day" with title "IRFC \\"XD\\"" sound name "Glass"'
    )


def test_not_on_macos_is_skipped_not_failed(monkeypatch, _no_real_notifications):
    monkeypatch.setattr(nc, "macos_available", lambda: False)
    ok, outcome, detail = nc.send_macos("t", "b")
    assert (ok, outcome) == (False, "skipped") and "not macOS" in detail
    assert _no_real_notifications == []


def test_osascript_failure_is_failed_with_stderr(monkeypatch):
    monkeypatch.setattr(nc, "macos_available", lambda: True)
    monkeypatch.setattr(nc, "_run", lambda args, **kw: subprocess.CompletedProcess(args, 1, "", "not authorised"))
    ok, outcome, detail = nc.send_macos("t", "b")
    assert (ok, outcome) == (False, "failed") and "not authorised" in detail


def _triggered_alert(db: Session) -> Alert:
    alert = Alert(alert_type="LENDING_OPPORTUNITY", is_active=True, is_triggered=True,
                  trigger_message="IRFC XD nets 6.82% p.a.")
    db.add(alert)
    db.commit()
    return alert


def test_delivery_logs_the_macos_attempt_with_its_outcome(db: Session, monkeypatch, _no_real_notifications):
    monkeypatch.setattr(nc, "macos_available", lambda: True)
    alert = _triggered_alert(db)
    results = AlertDeliveryService.deliver(db, [alert.id])
    mac = next(r for r in results if r.channel == "macos")
    assert (mac.delivered, mac.outcome) == (True, "sent")
    row = db.query(NotificationDelivery).filter_by(channel="macos").one()
    assert (row.delivered, row.outcome, row.alert_id) == (True, "sent", alert.id)
    assert "IRFC XD nets 6.82% p.a." in _no_real_notifications[-1][2]


def test_macos_channel_can_be_turned_off(db: Session, monkeypatch, client):
    monkeypatch.setattr(nc, "macos_available", lambda: True)
    assert client.put("/api/settings", json={"macos_notifications_enabled": False}).status_code == 200
    alert = _triggered_alert(db)
    mac = next(r for r in AlertDeliveryService.deliver(db, [alert.id]) if r.channel == "macos")
    assert (mac.delivered, mac.outcome) == (False, "skipped") and "turned off" in mac.detail
