"""Notification channel status (Phase 5). Reveals only whether each channel is
on, never a credential, number or address."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from slbm_tracker.db.session import get_db
from slbm_tracker.services import business_config, notification_channels
from slbm_tracker.services.alert_delivery_service import AlertDeliveryService

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("/status", response_model=dict[str, Any])
def notification_status() -> dict[str, Any]:
    return {
        "macos_available": notification_channels.macos_available(),
        "macos_enabled": bool(business_config.effective("macos_notifications_enabled")),
        "whatsapp_configured": notification_channels.whatsapp_configured(),
        "email_configured": AlertDeliveryService.email_channel_active(),
    }


@router.post("/test", response_model=dict[str, Any])
def send_test_notification(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Fire a test on the macOS channel, plus WhatsApp when configured. Each
    attempt is logged; the response carries outcomes only, never credentials."""
    results = AlertDeliveryService.send_test(db)
    return {
        "results": [
            {"channel": r.channel, "delivered": r.delivered, "outcome": r.outcome, "detail": r.detail}
            for r in results
        ]
    }
