"""Alert notification delivery (Phase 3 feature 5).

Delivery is deliberately SEPARATE from evaluation
(:mod:`slbm_tracker.services.alert_service`): evaluation persists the trigger
first, then delivery is attempted best-effort. A delivery failure must never
destroy or roll back the underlying trigger, so every channel send is wrapped
and failures are logged and returned as results, never raised.

Channels
--------
* **in-app** (always available): the trigger row itself IS the in-app
  notification — it already surfaces in the dashboard "recent alerts" feed and
  the Alerts page. This channel therefore just confirms the trigger is
  persisted; it needs no external system, credentials or network.
* **email/SMTP** (Phase 5A — active, but OFF until configured): fired alerts are
  emailed via stdlib :mod:`smtplib` when ``notifications_enabled`` AND
  ``smtp_host`` AND ``alert_email_to`` are all set. Credentials come from
  environment variables (``SMTP_*``) ONLY — never hardcoded, never stored in the
  DB, never returned to the frontend. A send failure is caught and recorded as
  ``delivered=False``; it never raises out or affects the trigger.

* **WhatsApp** (Phase 5.3): via Twilio, only for LENDING_OPPORTUNITY and
  ROLLOVER, on only when TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, WHATSAPP_FROM
  and WHATSAPP_TO are all set in the environment (never stored or returned).
* **macOS** (Phase 5.1): a desktop notification via osascript; skipped (not
  failed) when not on a Mac or when ``macos_notifications_enabled`` is off.

Every attempt is logged with an ``outcome`` of sent, skipped or failed. No web
push (decided 4 Oct 2026: the app runs on the Mac only).
"""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Optional

from sqlalchemy.orm import Session

from slbm_tracker.config import get_settings
from slbm_tracker.db.models import Alert, NotificationDelivery
from slbm_tracker.services import business_config, notification_channels

logger = logging.getLogger(__name__)

CHANNEL_IN_APP = "in_app"
CHANNEL_EMAIL = "email"
CHANNEL_MACOS = "macos"
CHANNEL_WHATSAPP = "whatsapp"
# Phase 5.3: only these reach the phone (the ones worth acting on away from the Mac).
WHATSAPP_ALERT_TYPES = frozenset({"LENDING_OPPORTUNITY", "ROLLOVER"})


@dataclass(frozen=True)
class DeliveryResult:
    """Outcome of attempting to deliver one triggered alert on one channel."""

    alert_id: Optional[int]
    channel: str
    delivered: bool
    detail: str
    # sent | skipped | failed; derived from ``delivered`` when not given.
    outcome: str = ""

    def __post_init__(self) -> None:
        if not self.outcome:
            object.__setattr__(self, "outcome", "sent" if self.delivered else "skipped")


class AlertDeliveryService:
    """Best-effort fan-out of triggered alerts to notification channels.

    Never raises for a channel failure; returns a :class:`DeliveryResult` per
    attempt so callers can report/log without risking the persisted trigger.
    """

    @staticmethod
    def _email_enabled() -> bool:
        """Whether the SMTP channel is configured to actually send.

        Requires the master ``notifications_enabled`` switch AND both an
        ``smtp_host`` (where to send) AND an ``alert_email_to`` (who to send to).
        Any missing piece leaves email OFF and it is recorded as skipped, never
        attempted. ``notifications_enabled`` honors a runtime override; SMTP
        settings are read from the environment only.
        """
        settings = get_settings()
        host = getattr(settings, "smtp_host", "") or ""
        to_addr = getattr(settings, "alert_email_to", "") or ""
        return (
            bool(business_config.effective("notifications_enabled"))
            and bool(host.strip())
            and bool(to_addr.strip())
        )

    @classmethod
    def email_channel_active(cls) -> bool:
        """Public, non-secret view of whether email delivery is live.

        Safe to surface in API responses / the UI: it reveals only that the
        channel is configured, never any credential or address.
        """
        return cls._email_enabled()

    @classmethod
    def _deliver_in_app(cls, alert: Alert) -> DeliveryResult:
        """In-app delivery: the persisted trigger is the notification."""
        if alert.is_triggered:
            return DeliveryResult(
                alert_id=alert.id,
                channel=CHANNEL_IN_APP,
                delivered=True,
                detail="Surfaced in-app (dashboard recent-alerts / Alerts page).",
            )
        return DeliveryResult(
            alert_id=alert.id,
            channel=CHANNEL_IN_APP,
            delivered=False,
            detail="Alert is not in a triggered state; nothing to surface.",
        )

    @staticmethod
    def _build_email(alert: Alert) -> EmailMessage:
        """Build a plain-text alert email from the persisted trigger message.

        Uses the SAME ``trigger_message`` already shown in-app, so the email and
        the app never disagree. From/To come from env settings.
        """
        settings = get_settings()
        from_addr = (
            getattr(settings, "smtp_from_addr", "")
            or getattr(settings, "smtp_user", "")
            or getattr(settings, "alert_email_to", "")
        )
        body = alert.trigger_message or f"Alert {alert.id} triggered."
        msg = EmailMessage()
        msg["Subject"] = f"[SLBM Tracker] Alert triggered: {alert.alert_type}"
        msg["From"] = from_addr
        msg["To"] = getattr(settings, "alert_email_to", "")
        msg.set_content(body)
        return msg

    @staticmethod
    def _smtp_send(msg: EmailMessage) -> None:
        """Send one message via SMTP. Raises on any transport/auth failure.

        Non-context-manager form (explicit ``quit()`` in ``finally``) so tests
        can ``mock.patch('smtplib.SMTP')`` and assert on ``send_message``.
        """
        settings = get_settings()
        host = getattr(settings, "smtp_host", "")
        port = int(getattr(settings, "smtp_port", 587) or 587)
        timeout = float(getattr(settings, "nse_request_timeout_seconds", 10.0) or 10.0)
        server = smtplib.SMTP(host, port, timeout=timeout)
        try:
            server.ehlo()
            if getattr(settings, "smtp_use_tls", True):
                server.starttls()
                server.ehlo()
            user = getattr(settings, "smtp_user", "") or ""
            password = getattr(settings, "smtp_password", "") or ""
            if user and password:
                server.login(user, password)
            server.send_message(msg)
        finally:
            try:
                server.quit()
            except Exception:  # noqa: BLE001 - closing must never mask the send outcome
                pass

    @classmethod
    def _deliver_email(cls, alert: Alert) -> DeliveryResult:
        """Email delivery via stdlib SMTP. Never raises; failures are captured."""
        if not cls._email_enabled():
            return DeliveryResult(
                alert_id=alert.id,
                channel=CHANNEL_EMAIL,
                delivered=False,
                detail="Email channel not configured (SMTP disabled).",
            )
        try:
            cls._smtp_send(cls._build_email(alert))
        except Exception as exc:  # noqa: BLE001 - delivery must never raise out
            logger.warning("Email delivery failed for alert %s: %s", alert.id, exc)
            return DeliveryResult(
                alert_id=alert.id,
                channel=CHANNEL_EMAIL,
                delivered=False,
                detail=f"SMTP send failed: {exc}",
                outcome="failed",
            )
        return DeliveryResult(
            alert_id=alert.id,
            channel=CHANNEL_EMAIL,
            delivered=True,
            detail=f"Emailed to {get_settings().alert_email_to}.",
        )

    @staticmethod
    def _title(alert_type: str) -> str:
        return f"SLBM: {alert_type.replace('_', ' ').title()}"

    @classmethod
    def _deliver_macos(cls, alert_id: Optional[int], title: str, body: str) -> DeliveryResult:
        """Desktop notification on this Mac. Never raises."""
        if not business_config.effective("macos_notifications_enabled"):
            return DeliveryResult(alert_id, CHANNEL_MACOS, False, "skipped: macOS notifications turned off", "skipped")
        ok, outcome, detail = notification_channels.send_macos(title, body)
        return DeliveryResult(alert_id, CHANNEL_MACOS, ok, detail, outcome)

    @classmethod
    def _deliver_whatsapp(cls, alert_id: Optional[int], alert_type: Optional[str], title: str, body: str) -> DeliveryResult:
        """WhatsApp via Twilio, only for LENDING_OPPORTUNITY and ROLLOVER (or a
        test message). Credentials stay in the environment. Never raises."""
        if alert_type is not None and alert_type not in WHATSAPP_ALERT_TYPES:
            return DeliveryResult(
                alert_id, CHANNEL_WHATSAPP, False,
                "skipped: WhatsApp is only for LENDING_OPPORTUNITY and ROLLOVER", "skipped",
            )
        ok, outcome, detail = notification_channels.send_whatsapp(f"*{title}*\n{body}")
        return DeliveryResult(alert_id, CHANNEL_WHATSAPP, ok, detail, outcome)

    @classmethod
    def send_test(cls, db: Session) -> list[DeliveryResult]:
        """'Send a test notification' (Phase 5.5): macOS, plus WhatsApp when
        configured. Logged like any delivery, with no alert attached."""
        title = "SLBM Tracker test"
        body = "Test notification: if you can read this, alerts will reach you here."
        results = [
            cls._deliver_macos(None, title, body),
            cls._deliver_whatsapp(None, None, title, body),
        ]
        cls._record(db, results)
        return results

    @classmethod
    def macos_channel_active(cls) -> bool:
        return notification_channels.macos_available() and bool(
            business_config.effective("macos_notifications_enabled")
        )

    @classmethod
    def _record(cls, db: Session, results: list[DeliveryResult]) -> None:
        """Persist delivery outcomes to the audit log. Best-effort, never raises.

        Called AFTER triggers are already committed upstream, so committing these
        rows here cannot affect trigger state. A failure to write the audit log
        must not break delivery, so it is caught, rolled back and logged.
        """
        if not results:
            return
        try:
            for r in results:
                db.add(
                    NotificationDelivery(
                        alert_id=r.alert_id,
                        channel=r.channel,
                        delivered=r.delivered,
                        detail=r.detail,
                        outcome=r.outcome,
                    )
                )
            db.commit()
        except Exception as exc:  # noqa: BLE001 - audit write must never break delivery
            logger.warning("Failed to persist delivery audit log: %s", exc)
            db.rollback()

    @classmethod
    def deliver(cls, db: Session, alert_ids: Optional[list[int]] = None) -> list[DeliveryResult]:
        """Deliver currently-triggered alerts. Best-effort, never raises.

        If ``alert_ids`` is given, only those are considered (still filtered to
        triggered). Otherwise every triggered alert is delivered. The master
        ``notifications_enabled`` switch gates delivery entirely (in-app
        included), but does NOT affect evaluation/persistence. Every attempt is
        appended to the ``notification_deliveries`` audit log before returning.
        """
        if not business_config.effective("notifications_enabled"):
            logger.info("Notifications disabled; skipping alert delivery.")
            return []

        q = db.query(Alert).filter(Alert.is_triggered.is_(True))
        if alert_ids is not None:
            if not alert_ids:
                return []
            q = q.filter(Alert.id.in_(alert_ids))
        triggered = q.all()

        results: list[DeliveryResult] = []
        for alert in triggered:
            # Each channel is independent and isolated; one failing does not
            # affect the other or the trigger.
            results.append(cls._deliver_in_app(alert))
            results.append(cls._deliver_email(alert))
            title = cls._title(alert.alert_type)
            body = alert.trigger_message or f"Alert {alert.id} triggered."
            results.append(cls._deliver_macos(alert.id, title, body))
            results.append(cls._deliver_whatsapp(alert.id, alert.alert_type, title, body))

        cls._record(db, results)
        return results
