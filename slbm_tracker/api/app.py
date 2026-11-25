"""FastAPI application factory and middleware configuration."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from slbm_tracker import __version__
from slbm_tracker.api.routes import (
    alerts,
    analytics,
    contracts,
    dashboard,
    export,
    health,
    notifications,
    opportunities,
    pnl,
    portfolio,
    rollover,
    settings,
    slb,
    symbols,
    websocket,
)
from slbm_tracker.api.ws_manager import ws_manager
from slbm_tracker.api.routes import scheduler as scheduler_routes
from slbm_tracker.services.power_service import PowerService
from slbm_tracker.services.slb_history_service import SLBHistoryArchive
from slbm_tracker.config import ALLOWED_ORIGINS, get_settings
from slbm_tracker.db.session import SessionLocal, init_db
from slbm_tracker.logging_config import setup_logging
from slbm_tracker.services import business_config
from slbm_tracker.services.scheduler_service import AlertSchedulerService
from slbm_tracker.services.symbol_universe_service import SymbolUniverseService

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifecycle events: startup and shutdown."""
    settings = get_settings()
    app.state.power = PowerService()
    app.state.slb_history = SLBHistoryArchive()
    setup_logging(log_level=settings.log_level)
    # Initialize SQLite database schema
    init_db()
    # Seed the tracked SLB symbol universe. Idempotent: re-running adds nothing
    # once the universe is populated. Failures here must not take down the app,
    # so they are logged rather than propagated.
    db = SessionLocal()
    try:
        SymbolUniverseService.seed_symbols(db)
    except Exception:  # noqa: BLE001 - startup seeding is best-effort
        logger.exception("Failed to seed tracked symbol universe on startup.")
    # Phase 5D: warm the editable-settings override cache from the DB so any
    # persisted business-pref overrides take effect immediately — before the
    # first request or scheduler tick — rather than only after the next PUT.
    # Best-effort: a failure here must not take down startup.
    try:
        business_config.refresh(db)
    except Exception:  # noqa: BLE001 - override warm-up is best-effort
        logger.exception("Failed to load business-setting overrides on startup.")
    finally:
        db.close()

    # Phase 4 real-time push: bind the running loop ALWAYS (even when the
    # scheduler is disabled) so the synchronous POST /api/alerts/evaluate route
    # can still reach the loop via ws_manager.submit().
    loop = asyncio.get_running_loop()
    ws_manager.bind_loop(loop)

    # Phase 4 background monitor: start the auto-evaluation loop if enabled.
    # Startup failures are best-effort/logged — the app must boot regardless.
    app.state.scheduler = None
    if settings.alert_scheduler_enabled:
        try:
            scheduler = AlertSchedulerService(settings=settings)
            # Register on app.state BEFORE start() so that a partial start (the
            # loop task gets spawned, then a subsequent line raises) is still torn
            # down by the shutdown path below. scheduler.stop() is safe to call
            # even if the loop never actually began (idempotent cancel + client
            # close), so this can only ever help, never double-stop.
            app.state.scheduler = scheduler
            scheduler.start(loop)
        except Exception:  # noqa: BLE001 - scheduler start is best-effort
            logger.exception("Failed to start the alert scheduler on startup.")

    try:
        yield
    finally:
        await app.state.slb_history.close()
        app.state.power.set_enabled(False)
        scheduler = getattr(app.state, "scheduler", None)
        if scheduler is not None:
            try:
                await scheduler.stop()
            except Exception:  # noqa: BLE001 - shutdown must never raise
                logger.exception("Error while stopping the alert scheduler.")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="NSE SLBM Order Tracker API",
        description=(
            "REST API for tracking NSE Securities Lending & Borrowing Mechanism (SLBM) "
            "opportunities, managing stock holdings, contracts, and P&L."
        ),
        version=__version__,
        lifespan=lifespan,
    )

    # Enable CORS for local desktop/web frontend. The same allowlist guards the
    # /api/ws WebSocket handshake (see routes/websocket.py) so REST and WS agree.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(ALLOWED_ORIGINS),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register API routes under /api
    app.include_router(health.router, prefix="/api")
    app.include_router(scheduler_routes.router, prefix="/api")
    app.include_router(portfolio.router, prefix="/api")
    app.include_router(slb.router, prefix="/api")
    app.include_router(dashboard.router, prefix="/api")
    app.include_router(opportunities.router, prefix="/api")
    app.include_router(notifications.router, prefix="/api")
    app.include_router(symbols.router, prefix="/api")
    app.include_router(contracts.router, prefix="/api")
    app.include_router(rollover.router, prefix="/api")
    app.include_router(alerts.router, prefix="/api")
    app.include_router(pnl.router, prefix="/api")
    app.include_router(analytics.router, prefix="/api")
    app.include_router(export.router, prefix="/api")
    app.include_router(settings.router, prefix="/api")
    # Real-time push endpoint resolves to /api/ws.
    app.include_router(websocket.router, prefix="/api")

    # Phase 7.1: serve the built frontend (after every /api route) so the app is
    # one address, http://127.0.0.1:8010.
    _mount_frontend(app, Path(get_settings().frontend_dist_dir))

    return app


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    """Serve ``dist``: real files as themselves, any other non-API path as
    index.html (so /live and /slb/IRFC load on refresh). Unknown /api paths stay
    JSON 404s. Nothing is mounted when there is no build."""
    index = dist / "index.html"
    if not index.is_file():
        logger.info("No frontend build at %s; serving the API only.", dist)
        return
    root = dist.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        if full_path == "api" or full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        candidate = (root / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        return FileResponse(index, media_type="text/html")


app = create_app()
