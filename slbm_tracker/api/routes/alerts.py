"""Alert rule + evaluation API endpoints (Phase 3 features 4-5).

Rule CRUD plus an evaluate endpoint that runs the deterministic engine, persists
edge-triggered state, then attempts best-effort delivery. Delivery failures are
returned as data and never affect the persisted triggers or the HTTP status.
Notification credentials are never accepted or returned here.
"""

from __future__ import annotations

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from slbm_tracker.api.schemas import (
    AlertCreate,
    AlertDeliveryLogResponse,
    AlertDeliveryResultResponse,
    AlertEvaluationResult,
    AlertEvaluationRunResponse,
    AlertResponse,
    AlertUpdate,
    NotificationDeliveryResponse,
)
from slbm_tracker.api.ws_manager import ws_manager
from slbm_tracker.db.models import Alert, NotificationDelivery
from slbm_tracker.db.session import get_db
from slbm_tracker.services.alert_delivery_service import AlertDeliveryService
from slbm_tracker.services.alert_service import AlertService
from slbm_tracker.services.scheduler_service import AlertSchedulerService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/alerts", tags=["Alerts"])


@router.get("", response_model=list[AlertResponse])
def list_alerts(
    active_only: bool = Query(False, description="Only return active rules"),
    db: Session = Depends(get_db),
) -> list[AlertResponse]:
    """List configured alert rules with their latest trigger state."""
    return AlertService.list_alerts(db=db, active_only=active_only)


@router.post("", response_model=AlertResponse, status_code=status.HTTP_201_CREATED)
def create_alert(payload: AlertCreate, db: Session = Depends(get_db)) -> AlertResponse:
    """Create an alert rule."""
    try:
        return AlertService.create_alert(
            db=db,
            alert_type=payload.alert_type,
            threshold=payload.threshold,
            stock_id=payload.stock_id,
            is_active=payload.is_active,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )


@router.patch("/{alert_id}", response_model=AlertResponse)
def update_alert(
    alert_id: int, payload: AlertUpdate, db: Session = Depends(get_db)
) -> AlertResponse:
    """Enable or disable an alert rule."""
    alert = AlertService.set_active(db=db, alert_id=alert_id, is_active=payload.is_active)
    if alert is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Alert {alert_id} not found."
        )
    return alert


@router.delete("/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_alert(alert_id: int, db: Session = Depends(get_db)) -> None:
    """Delete an alert rule."""
    if not AlertService.delete_alert(db=db, alert_id=alert_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Alert {alert_id} not found."
        )


@router.post("/evaluate", response_model=AlertEvaluationRunResponse)
def evaluate_alerts(
    deliver: bool = Query(True, description="Attempt best-effort delivery after evaluation"),
    db: Session = Depends(get_db),
) -> AlertEvaluationRunResponse:
    """Evaluate all active rules, persist triggers, then optionally deliver.

    Triggers are committed by the evaluation step before delivery is attempted,
    so a delivery failure cannot destroy them.
    """
    results = AlertService.evaluate_all(db=db)
    fired_ids = [r.alert_id for r in results if r.fired_now]

    deliveries: list[AlertDeliveryResultResponse] = []
    if deliver:
        delivery_results = AlertDeliveryService.deliver(db=db, alert_ids=fired_ids)
        deliveries = [
            AlertDeliveryResultResponse(
                alert_id=d.alert_id, channel=d.channel, delivered=d.delivered, detail=d.detail,
                outcome=d.outcome,
            )
            for d in delivery_results
        ]

    # Live-feed push (Phase 4): fire-and-forget and thread-safe, so it never
    # blocks or breaks this sync request. evaluate_all has already committed
    # last_triggered_at, so each frame carries authoritative persisted state and
    # uses the SAME envelope the background scheduler emits. If no event loop is
    # bound (a pure unit test) submit() logs and skips.
    if fired_ids:
        fired_alerts = db.query(Alert).filter(Alert.id.in_(fired_ids)).all()
        for alert in fired_alerts:
            ws_manager.submit(AlertSchedulerService.build_alert_fired_message(alert))

    return AlertEvaluationRunResponse(
        evaluated=len(results),
        fired=len(fired_ids),
        results=[
            AlertEvaluationResult(
                alert_id=r.alert_id,
                alert_type=r.alert_type,
                stock_id=r.stock_id,
                threshold_used=r.threshold_used,
                condition_met=r.condition_met,
                fired_now=r.fired_now,
                message=r.message,
            )
            for r in results
        ],
        deliveries=deliveries,
    )


@router.get("/deliveries", response_model=AlertDeliveryLogResponse)
def list_deliveries(
    limit: int = Query(50, ge=1, le=500, description="Max recent rows, newest first"),
    db: Session = Depends(get_db),
) -> AlertDeliveryLogResponse:
    """Recent notification-delivery audit log (newest first) + email status.

    Read-only. Exposes only outcome data (channel / delivered / detail / time)
    and a non-secret ``email_channel_active`` flag — never SMTP credentials.
    """
    rows = (
        db.query(NotificationDelivery)
        .order_by(NotificationDelivery.created_at.desc(), NotificationDelivery.id.desc())
        .limit(limit)
        .all()
    )
    return AlertDeliveryLogResponse(
        email_channel_active=AlertDeliveryService.email_channel_active(),
        deliveries=[NotificationDeliveryResponse.model_validate(r) for r in rows],
    )
