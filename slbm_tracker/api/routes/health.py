"""Health and diagnostics route."""

from __future__ import annotations

import datetime
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from slbm_tracker import __version__
from slbm_tracker.api.schemas import HealthResponse
from slbm_tracker.db.session import get_db

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("", response_model=HealthResponse)
def health_check(db: Session = Depends(get_db)) -> HealthResponse:
    """Check API and database connectivity status."""
    db_status = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"error: {e}"

    return HealthResponse(
        status="healthy" if db_status == "ok" else "degraded",
        version=__version__,
        database=db_status,
        timestamp=datetime.datetime.utcnow(),
    )
