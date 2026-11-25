"""Dashboard summary API endpoint."""

from __future__ import annotations

import logging
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from slbm_tracker.api.schemas import DashboardSummaryResponse
from slbm_tracker.db.session import get_db
from slbm_tracker.services.dashboard_service import DashboardService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/summary", response_model=DashboardSummaryResponse)
def get_dashboard_summary(db: Session = Depends(get_db)) -> DashboardSummaryResponse:
    """Aggregated overview: portfolio totals, contract status, income, upcoming
    expiries, best opportunities, and recent alerts."""
    summary = DashboardService.build_summary(db=db)
    return DashboardSummaryResponse(**summary)
