"""Test configuration, fixtures, and mock data."""

from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Generator

# Point the app's global engine at a throwaway file BEFORE slbm_tracker is
# imported (the engine is built at import time). The ``client`` fixture enters
# the app lifespan, which runs init_db() (create_all + additive migrations) and
# seeds symbols on that engine; without this, a test run would migrate and write
# to the user's live slbm_tracker.db. An env var outranks the .env file.
_TEST_DB_DIR = tempfile.mkdtemp(prefix="slbm-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_TEST_DB_DIR, 'lifespan.db')}"
# Phase 7.4: the suite never writes daily backups (they would land in
# ~/Library/Application Support/slbm-tracker/backups). Tests opt in with a temp dir.
os.environ["HISTORY_CACHE_DIR"] = os.path.join(_TEST_DB_DIR, "slb-history")
os.environ["BACKUPS_ENABLED"] = "false"
os.environ["BACKUP_DIR"] = os.path.join(_TEST_DB_DIR, "backups")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from slbm_tracker.api.app import create_app
from slbm_tracker.config import get_settings
from slbm_tracker.db.session import Base, get_db
from slbm_tracker.services import business_config


@pytest.fixture(autouse=True)
def _reset_business_config():
    """Clear the Phase 5D editable-settings override cache around every test.

    ``business_config`` keeps a module-level ``_overrides`` cache layered on top
    of the env config. A test that PUTs an override — or the ``client`` lifespan
    warming the cache from the real on-disk DB — would otherwise leak that
    override into later tests. Clearing before and after each test guarantees
    every test starts from pure environment defaults, exactly as the code behaved
    before 5D routed reads through the resolver.
    """
    business_config.reset_cache()
    try:
        yield
    finally:
        business_config.reset_cache()


@pytest.fixture(autouse=True)
def _disable_alert_scheduler():
    """Keep the Phase 4 background scheduler OFF for the whole suite.

    The ``client`` fixture enters the app lifespan (``with TestClient(app)``),
    which would otherwise start the auto-evaluation loop and construct a real
    NSEClient — non-deterministic and network-touching. Forcing the flag off on
    the cached settings singleton keeps every existing test deterministic and
    offline. The loop's behavior is covered explicitly in
    ``test_scheduler_service.py`` by calling ``run_one_tick()`` / ``start()``
    directly, which do not consult this flag.
    """
    settings = get_settings()
    original = settings.alert_scheduler_enabled
    settings.alert_scheduler_enabled = False
    try:
        yield
    finally:
        settings.alert_scheduler_enabled = original


# In-memory SQLite engine for fast, isolated tests
@pytest.fixture(name="db_engine")
def db_engine_fixture():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(name="db")
def db_fixture(db_engine) -> Generator[Session, None, None]:
    session_factory = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
    session = session_factory()

    yield session

    session.close()


@pytest.fixture(name="client")
def client_fixture(db: Session) -> Generator[TestClient, None, None]:
    app = create_app()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as c:
        # The lifespan may have warmed business_config from the real on-disk DB;
        # reset it so every client-based test starts from pure env defaults. A
        # test that PUTs settings repopulates the cache from its own in-memory DB
        # via the settings route.
        business_config.reset_cache()
        yield c

    app.dependency_overrides.clear()


@pytest.fixture(name="sample_nse_json")
def sample_nse_json_fixture() -> dict:
    """Authentic NSE SLB JSON payload structure discovered from live testing."""
    return {
        "data": [
            {
                "symbol": "RELIANCE",
                "buyOrderPrice1": 4.50,
                "buyOrderQty1": 500,
                "sellOrderPrice1": 5.25,
                "sellQty1": 1000,
                "lastTradedPrice": 5.00,
                "underLyingLtp": 2850.50,
                "futuresLtp": 2860.00,
                "spread": 0.75,
                "spreadPer": 14.28,
                "openPositions": 15200,
                "annualisedYieldPer": 8.55,
                "volume": 3500,
                "turnOver": 17500.0,
                "transactionValue": 9976750.0,
                "caExpDate": "05-Jun-2026",
                "ca": "DIVIDEND - RS 6 PER SHARE",
                "meta": {
                    "isSLBSec": True
                },
                "series": "Oct-2026",
            },
            {
                "symbol": "RELIANCE",
                "buyOrderPrice1": 8.00,
                "buyOrderQty1": 200,
                "sellOrderPrice1": 9.50,
                "sellQty1": 400,
                "lastTradedPrice": 8.50,
                "underLyingLtp": 2850.50,
                "futuresLtp": 2875.00,
                "spread": 1.50,
                "spreadPer": 17.65,
                "openPositions": 8500,
                "annualisedYieldPer": 7.80,
                "volume": 1200,
                "turnOver": 10200.0,
                "transactionValue": 3420600.0,
                "caExpDate": None,
                "ca": None,
                "meta": {
                    "isSLBSec": True
                },
                "series": "Nov-2026",
            },
        ]
    }


@pytest.fixture(name="zerodha_console_csv")
def zerodha_console_csv_fixture() -> str:
    """Sample CSV exported from Zerodha Console (Holdings)."""
    return (
        "Instrument,Qty.,Avg. cost,LTP,Cur. val,P&L,Net chg.,Day chg.\n"
        "RELIANCE,50,2450.50,2850.00,142500.00,19975.00,16.30,1.2\n"
        "INFY,120,1350.00,1620.00,194400.00,32400.00,20.00,-0.5\n"
        "TCS,25,3200.00,3800.00,95000.00,15000.00,18.75,0.8\n"
        "Total,195,-,-,431900.00,67375.00,-,-\n"
    )


@pytest.fixture(name="zerodha_kite_csv")
def zerodha_kite_csv_fixture() -> str:
    """Sample CSV exported from Zerodha Kite web holding table."""
    return (
        "Symbol,ISIN,Quantity Available,Total Quantity,Average Price\n"
        "NSE:HDFCBANK,INE040A01034,75,75,1520.40\n"
        "NSE:ITC,INE154A01025,200,200,430.15\n"
    )


SNAPSHOT_TODAY = __import__("datetime").date(2026, 10, 1)


@pytest.fixture()
def pinned_snapshot_today(monkeypatch):
    """Pin 'today' for the latest-snapshot readers to 1 Oct 2026.

    Since Phase 1.3 those readers drop series whose expiry is before today, so
    tests that seed Nov-2026 snapshots and evaluate against the real clock would
    start failing once that series expires. Modules opt in with
    ``pytestmark = pytest.mark.usefixtures("pinned_snapshot_today")``.
    """
    from slbm_tracker.services import alert_service, dashboard_service

    monkeypatch.setattr(alert_service, "today_ist", lambda: SNAPSHOT_TODAY)
    monkeypatch.setattr(dashboard_service, "today_ist", lambda: SNAPSHOT_TODAY)
    return SNAPSHOT_TODAY


@pytest.fixture(autouse=True)
def _no_real_notifications(monkeypatch):
    """Phase 5: never pop a real macOS notification or send a real WhatsApp
    message from the suite (it runs on the user's Mac, and .env may hold Twilio
    credentials). osascript is replaced by a recorder; the Twilio credentials on
    the cached settings are blanked and the HTTP sender refuses to run. Tests
    that exercise a channel install their own stubs over these."""
    from slbm_tracker.services import notification_channels

    calls: list[list[str]] = []

    def fake_osascript(args, **kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    def refuse_http(*args, **kwargs):
        raise AssertionError("A test tried to send a real WhatsApp message.")

    monkeypatch.setattr(notification_channels, "_run", fake_osascript)
    monkeypatch.setattr(notification_channels, "_http_post", refuse_http)
    settings = get_settings()
    for name in ("twilio_account_sid", "twilio_auth_token", "whatsapp_from", "whatsapp_to"):
        monkeypatch.setattr(settings, name, "")
    return calls
