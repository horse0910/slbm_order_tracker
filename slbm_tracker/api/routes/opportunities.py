"""Lending opportunities API (Phase 3.5)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from slbm_tracker.db.session import get_db
from slbm_tracker.services import opportunity_service

router = APIRouter(prefix="/opportunities", tags=["Opportunities"])


@router.get("", response_model=dict[str, Any])
def list_opportunities(
    expiries: Optional[int] = Query(
        None, ge=1, le=6, description="Series B expiries per stock (default: tracked_expiries)"
    ),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Each held stock across its next Series B expiries, priced at the live bid
    on the fillable quantity, net of Zerodha's charges; actionable rows carry a
    ticket line, the rest list their reasons. Reads stored quotes only."""
    board = opportunity_service.build(
        db, expiries=expiries, today=opportunity_service.today_ist(), now=opportunity_service._utcnow()
    )
    return opportunity_service.to_json(board)
