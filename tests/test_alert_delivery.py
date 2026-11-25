"""Tests for alert delivery: separation from evaluation, best-effort behavior,
and that a delivery attempt never destroys the underlying trigger.
"""

from __future__ import annotations

import pytest

import datetime
from unittest import mock

from sqlalchemy.orm import Session

from slbm_tracker.config import Settings
from slbm_tracker.db.models import Alert, NotificationDelivery, SLBSnapshot
from slbm_tracker.services.alert_delivery_service import (
    CHANNEL_EMAIL,
    CHANNEL_IN_APP,
    AlertDeliveryService,
)
from slbm_tracker.services.alert_service import ALERT_YIELD_THRESHOLD, AlertService
from slbm_tracker.services import business_config

pytestmark = pytest.mark.usefixtures("pinned_snapshot_today")


def _configured_settings(**overrides) -> Settings:
    """A Settings with the SMTP email channel fully configured (for patching).

    Init kwargs take precedence over env/.env in pydantic-settings, so this is
    deterministic regardless of the ambient environment.
    """
    base = dict(
        notifications_enabled=True,
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="sender@example.com",
        smtp_password="app-pass",
        smtp_use_tls=True,
        smtp_from_addr="sender@example.com",
        alert_email_to="recipient@example.com",
    )
    base.update(overrides)
    return Settings(**base)


def _triggered_alert(db: Session) -> Alert:
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
    AlertService.evaluate_all(db=db)
    return db.query(Alert).filter(Alert.is_triggered.is_(True)).first()


def test_in_app_delivers_triggered_alert(db: Session):
    alert = _triggered_alert(db)
    results = AlertDeliveryService.deliver(db=db, alert_ids=[alert.id])
    in_app = [r for r in results if r.channel == CHANNEL_IN_APP]
    assert len(in_app) == 1
    assert in_app[0].delivered is True


def test_email_seam_is_present_but_not_enabled(db: Session):
    alert = _triggered_alert(db)
    results = AlertDeliveryService.deliver(db=db, alert_ids=[alert.id])
    email = [r for r in results if r.channel == CHANNEL_EMAIL]
    assert len(email) == 1
    assert email[0].delivered is False  # seam only; never raises


def test_delivery_does_not_mutate_the_trigger(db: Session):
    alert = _triggered_alert(db)
    stamp = alert.last_triggered_at
    AlertDeliveryService.deliver(db=db, alert_ids=[alert.id])
    db.refresh(alert)
    # Delivery is read-only w.r.t. the trigger: state and timestamp intact.
    assert alert.is_triggered is True
    assert alert.last_triggered_at == stamp


def test_deliver_only_targets_triggered_alerts(db: Session):
    # An untriggered alert is never delivered even if its id is passed.
    AlertService.create_alert(db=db, alert_type=ALERT_YIELD_THRESHOLD, threshold=8.0)
    untriggered = db.query(Alert).first()
    results = AlertDeliveryService.deliver(db=db, alert_ids=[untriggered.id])
    assert results == []


def test_empty_id_list_delivers_nothing(db: Session):
    _triggered_alert(db)
    assert AlertDeliveryService.deliver(db=db, alert_ids=[]) == []


def test_email_channel_active_reflects_configuration(db: Session):
    # Not configured in the test environment.
    assert AlertDeliveryService.email_channel_active() is False
    with mock.patch(
        "slbm_tracker.services.alert_delivery_service.get_settings",
        return_value=_configured_settings(),
    ):
        assert AlertDeliveryService.email_channel_active() is True


def test_email_disabled_path_does_not_attempt_send(db: Session):
    alert = _triggered_alert(db)
    with mock.patch("smtplib.SMTP") as smtp_cls:
        results = AlertDeliveryService.deliver(db=db, alert_ids=[alert.id])
    email = [r for r in results if r.channel == CHANNEL_EMAIL]
    assert email[0].delivered is False
    smtp_cls.assert_not_called()  # disabled channel never opens a connection


def test_email_delivers_when_configured(db: Session):
    alert = _triggered_alert(db)
    body = alert.trigger_message
    cfg = _configured_settings()
    with mock.patch(
        "slbm_tracker.services.alert_delivery_service.get_settings", return_value=cfg
    ), mock.patch("smtplib.SMTP") as smtp_cls:
        results = AlertDeliveryService.deliver(db=db, alert_ids=[alert.id])

    email = [r for r in results if r.channel == CHANNEL_EMAIL]
    assert len(email) == 1
    assert email[0].delivered is True

    # Connected to the configured host/port with a timeout.
    smtp_cls.assert_called_once_with(
        "smtp.example.com", 587, timeout=cfg.nse_request_timeout_seconds
    )
    server = smtp_cls.return_value
    server.starttls.assert_called_once()
    server.login.assert_called_once_with("sender@example.com", "app-pass")
    server.send_message.assert_called_once()

    sent = server.send_message.call_args.args[0]
    assert sent["To"] == "recipient@example.com"
    assert sent["From"] == "sender@example.com"
    assert alert.alert_type in sent["Subject"]
    # Email body IS the persisted in-app trigger message (they never disagree).
    assert sent.get_content().strip() == (body or "").strip()


def test_email_no_login_when_credentials_absent(db: Session):
    alert = _triggered_alert(db)
    cfg = _configured_settings(smtp_user="", smtp_password="")
    with mock.patch(
        "slbm_tracker.services.alert_delivery_service.get_settings", return_value=cfg
    ), mock.patch("smtplib.SMTP") as smtp_cls:
        AlertDeliveryService.deliver(db=db, alert_ids=[alert.id])
    server = smtp_cls.return_value
    server.login.assert_not_called()  # no creds → anonymous relay, still sends
    server.send_message.assert_called_once()


def test_email_no_starttls_when_disabled(db: Session):
    alert = _triggered_alert(db)
    cfg = _configured_settings(smtp_use_tls=False)
    with mock.patch(
        "slbm_tracker.services.alert_delivery_service.get_settings", return_value=cfg
    ), mock.patch("smtplib.SMTP") as smtp_cls:
        AlertDeliveryService.deliver(db=db, alert_ids=[alert.id])
    server = smtp_cls.return_value
    server.starttls.assert_not_called()
    server.send_message.assert_called_once()


def test_email_send_failure_is_captured_not_raised(db: Session):
    alert = _triggered_alert(db)
    cfg = _configured_settings()
    with mock.patch(
        "slbm_tracker.services.alert_delivery_service.get_settings", return_value=cfg
    ), mock.patch("smtplib.SMTP", side_effect=OSError("connection refused")):
        # Must not raise despite the SMTP failure.
        results = AlertDeliveryService.deliver(db=db, alert_ids=[alert.id])

    email = [r for r in results if r.channel == CHANNEL_EMAIL]
    assert email[0].delivered is False
    assert "SMTP send failed" in email[0].detail
    # One channel failing never affects the other.
    in_app = [r for r in results if r.channel == CHANNEL_IN_APP]
    assert in_app[0].delivered is True


def test_delivery_writes_audit_log(db: Session):
    alert = _triggered_alert(db)
    AlertDeliveryService.deliver(db=db, alert_ids=[alert.id])

    rows = db.query(NotificationDelivery).all()
    assert len(rows) == 4  # in_app + email + macos + whatsapp (Phase 5), one row each
    assert {r.channel for r in rows} == {CHANNEL_IN_APP, CHANNEL_EMAIL, "macos", "whatsapp"}
    for r in rows:
        assert r.alert_id == alert.id
        assert r.created_at is not None

    in_app_row = next(r for r in rows if r.channel == CHANNEL_IN_APP)
    assert in_app_row.delivered is True
    email_row = next(r for r in rows if r.channel == CHANNEL_EMAIL)
    assert email_row.delivered is False  # email not configured in test env


def test_notifications_disabled_skips_delivery_and_audit(db: Session):
    alert = _triggered_alert(db)
    # The master switch is now a runtime-editable business preference (Phase 5D),
    # resolved through business_config. Disabling it must skip delivery entirely
    # (in-app + email) and record nothing — the same contract as before.
    business_config.apply_updates(db, {"notifications_enabled": False})
    results = AlertDeliveryService.deliver(db=db, alert_ids=[alert.id])
    assert results == []
    # Master switch off → nothing attempted, nothing logged.
    assert db.query(NotificationDelivery).count() == 0
