"""Integration tests verifying full flow: API -> Services -> Database -> Fallback."""

import datetime
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from slbm_tracker.api.routes.slb import get_nse_service
from slbm_tracker.db.models import SLBSnapshot, Stock
from slbm_tracker.services.nse_client import NSEBlockedError, NSEClient, NSEConnectionError
from slbm_tracker.services.nse_service import NSEService


def test_full_portfolio_and_slb_tracking_flow(
    client: TestClient, db: Session, zerodha_console_csv: str, sample_nse_json: dict
):
    """
    End-to-End Integration Scenario:
    1. User uploads Zerodha holdings CSV via API.
    2. Stocks are parsed, validated, and persisted into the database.
    3. User queries live SLB opportunities for an imported stock (RELIANCE).
    4. Data is fetched via NSE service, parsed, and saved as historical SLBSnapshot records in DB.
    5. When the live NSE connection subsequently fails (e.g. 403 Bot Block), the system gracefully
       falls back to the cached snapshots in the database without crashing or returning empty data.
    """
    # 1. Upload Zerodha CSV
    files = {"file": ("zerodha_holdings.csv", zerodha_console_csv.encode("utf-8"), "text/csv")}
    res_import = client.post("/api/portfolio/import", files=files)
    assert res_import.status_code == 200
    import_data = res_import.json()
    assert import_data["added"] == 3

    # Verify stocks in database
    stocks_in_db = db.query(Stock).order_by(Stock.symbol).all()
    assert len(stocks_in_db) == 3
    assert [s.symbol for s in stocks_in_db] == ["INFY", "RELIANCE", "TCS"]

    # 2. Mock NSE Client returning live data
    mock_nse_client = AsyncMock(spec=NSEClient)
    mock_nse_client.fetch_slb_by_symbol.return_value = sample_nse_json

    nse_service = NSEService(db=db, client=mock_nse_client)
    client.app.dependency_overrides[get_nse_service] = lambda: nse_service

    try:
        # 3. Query SLB live quotes for RELIANCE
        res_slb = client.get("/api/slb/live/RELIANCE")
        assert res_slb.status_code == 200
        slb_data = res_slb.json()
        assert slb_data["symbol"] == "RELIANCE"
        assert slb_data["contracts_count"] == 2
        assert slb_data["contracts"][0]["annualized_yield"] == 8.55

        # 4. Verify snapshots persisted to database table
        snapshots_in_db = db.query(SLBSnapshot).filter(SLBSnapshot.symbol == "RELIANCE").all()
        assert len(snapshots_in_db) == 2
        assert snapshots_in_db[0].source == "NSE_API"
        assert snapshots_in_db[0].annualized_yield in (8.55, 7.80)

        # 5. Simulate NSE IP block (403 Forbidden) on next call
        mock_blocked_client = AsyncMock(spec=NSEClient)
        mock_blocked_client.fetch_slb_by_symbol.side_effect = NSEBlockedError("403 Forbidden: IP blocked by NSE")

        fallback_service = NSEService(db=db, client=mock_blocked_client)
        client.app.dependency_overrides[get_nse_service] = lambda: fallback_service

        res_fallback = client.get("/api/slb/live/RELIANCE")
        assert res_fallback.status_code == 200
        fallback_data = res_fallback.json()
        assert fallback_data["symbol"] == "RELIANCE"
        # Must return the cached database snapshots!
        assert fallback_data["contracts_count"] > 0
        assert fallback_data["contracts"][0]["symbol"] == "RELIANCE"
    finally:
        client.app.dependency_overrides.pop(get_nse_service, None)


def test_transport_error_falls_back_to_cache_without_500(client: TestClient, db: Session):
    """Regression: a non-HTTP transport failure (surfaced by the client as
    NSEConnectionError, e.g. from an httpx.ProxyError) must reach the cached
    fallback and return a structurally valid 200 — never an unexpected 500."""
    # Seed a cached snapshot the service can fall back to.
    db.add(
        SLBSnapshot(
            symbol="SBIN",
            series="Oct-2026",
            expiry_date=datetime.date(2026, 10, 6),
            best_bid=3.0,
            best_ask=3.5,
            ltp=3.25,
            volume=1000,
            open_positions=5000,
            annualized_yield=7.5,
            underlying_price=800.0,
            timestamp=datetime.datetime.utcnow(),
            source="NSE_API",
        )
    )
    db.commit()

    transport_failing_client = AsyncMock(spec=NSEClient)
    transport_failing_client.fetch_slb_by_symbol.side_effect = NSEConnectionError(
        "NSE network connection failed after 3 attempts: ProxyError('407 Proxy blocked')"
    )
    service = NSEService(db=db, client=transport_failing_client)
    client.app.dependency_overrides[get_nse_service] = lambda: service
    try:
        res = client.get("/api/slb/live/SBIN")
        # Must NOT 500 on a transport failure.
        assert res.status_code == 200
        body = res.json()
        # Structurally valid response backed by the cached snapshot.
        assert body["symbol"] == "SBIN"
        assert body["contracts_count"] == 1
        assert body["contracts"][0]["symbol"] == "SBIN"
        assert body["contracts"][0]["annualized_yield"] == 7.5
    finally:
        client.app.dependency_overrides.pop(get_nse_service, None)


def test_cached_fallback_restores_quantities_from_raw_data(db: Session, sample_nse_json: dict):
    """Cached fallback must restore order quantities / turnover from the
    persisted raw_data instead of showing misleading zeros."""
    from slbm_tracker.services.nse_parser import NSEDataParser

    service = NSEService(db=db, client=AsyncMock(spec=NSEClient))
    parsed = NSEDataParser.parse_api_response(sample_nse_json, default_symbol="RELIANCE")
    service._persist_snapshots(parsed, source="NSE_API")

    cached = service.get_cached_snapshots_for_symbol("RELIANCE")
    assert cached, "expected cached snapshots to be returned"
    top = cached[0]
    # These would be 0 under the old zero-defaulting behavior.
    assert top.best_bid_qty > 0
    assert top.best_ask_qty > 0
