"""Read-only scheduler health plus the explicit local keep-awake control."""
import datetime as dt
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from slbm_tracker.services import business_config
from slbm_tracker.services.contract_service import IST
from slbm_tracker.services.scheduler_service import is_market_hours

router = APIRouter(prefix="/scheduler", tags=["Monitoring"])

@router.get("/status")
def scheduler_status(request: Request):
    scheduler = getattr(request.app.state, "scheduler", None)
    now = dt.datetime.now(IST)
    backoff = getattr(scheduler, "_backoff_until", None)
    return {"running": bool(scheduler and scheduler.is_running), "market_open": is_market_hours(now),
            "refresh_seconds": int(business_config.effective("live_refresh_seconds")),
            "last_sweep": getattr(scheduler, "sweep_status", None),
            "last_tick": getattr(scheduler, "last_summary", None),
            "backoff_until": backoff, "generated_at": now.isoformat(),
            "scope": "held_and_actively_lent", "power": request.app.state.power.status()}

class AwakeRequest(BaseModel):
    enabled: bool

@router.put("/awake")
def keep_awake(payload: AwakeRequest, request: Request):
    try: return request.app.state.power.set_enabled(payload.enabled)
    except (ValueError, OSError) as exc: raise HTTPException(status_code=400, detail=str(exc))
