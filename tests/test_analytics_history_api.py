"""Phase 5B API tests: analytics history + equity-history routes.

Refresh is exercised with the service patched (AsyncMock), so no NSE call is
made — the fetch/parse/upsert pipeline itself is covered in
test_equity_history.py against a fake client.
"""

from __future__ import annotations

import datetime
from unittest import mock

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from slbm_tracker.db.models import SLBSnapshot, Stock, StockPriceHistory
from slbm_tracker.services.contract_service import today_ist


def test_history_symbol_scope(client: TestClient, db: Session):
    now = datetime.datetime.utcnow()
    db.add(SLBSnapshot(series_family="B",
        symbol="RELIANCE", expiry_date=datetime.date(2026, 11, 3), timestamp=now,
        annualized_yield=8.0, best_bid=4.0, best_ask=5.0, open_positions=100,
    ))
    db.commit()
    resp = client.get("/api/analytics/history?symbol=RELIANCE&days=30")
    assert resp.status_code == 200
    body = resp.json()
    assert body["scope"] == "symbol"
    assert body["symbol"] == "RELIANCE"
    assert len(body["points"]) == 1
    assert body["points"][0]["annualized_yield"] == 8.0


def test_history_portfolio_scope_default(client: TestClient, db: Session):
    resp = client.get("/api/analytics/history")
    assert resp.status_code == 200
    assert resp.json()["scope"] == "portfolio"


def test_history_days_clamped_to_retention(client: TestClient):
    # Default retention is 90 days; a larger (but schema-valid) request clamps.
    resp = client.get("/api/analytics/history?days=200")
    assert resp.status_code == 200
    assert resp.json()["days"] == 90


def test_equity_history_reads_persisted_bars_and_stats(client: TestClient, db: Session):
    base = today_ist()
    db.add_all([
        StockPriceHistory(symbol="RELIANCE", date=base - datetime.timedelta(days=2),
                          close=100.0, high=105, low=95, volume=1000),
        StockPriceHistory(symbol="RELIANCE", date=base - datetime.timedelta(days=1),
                          close=110.0, high=112, low=108, volume=2000),
    ])
    db.commit()
    resp = client.get("/api/analytics/equity-history?symbol=RELIANCE&days=30")
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "RELIANCE"
    assert len(body["bars"]) == 2
    assert body["stats"]["samples"] == 2
    assert body["stats"]["period_return_pct"] == 10.0  # (110-100)/100*100


def test_equity_history_empty_symbol_is_zero_samples(client: TestClient):
    resp = client.get("/api/analytics/equity-history?symbol=NOPE")
    assert resp.status_code == 200
    body = resp.json()
    assert body["bars"] == []
    assert body["stats"]["samples"] == 0


def test_refresh_one_symbol(client: TestClient):
    with mock.patch(
        "slbm_tracker.api.routes.analytics.EquityHistoryService.refresh_symbol",
        new=mock.AsyncMock(return_value=7),
    ):
        resp = client.post("/api/analytics/equity-history/refresh?symbol=RELIANCE")
    assert resp.status_code == 200
    body = resp.json()
    assert body["refreshed"] == {"RELIANCE": 7}
    assert body["symbols"] == 1 and body["total_bars"] == 7


def test_refresh_all_owned(client: TestClient):
    with mock.patch(
        "slbm_tracker.api.routes.analytics.EquityHistoryService.refresh_owned",
        new=mock.AsyncMock(return_value={"RELIANCE": 3, "INFY": 2}),
    ):
        resp = client.post("/api/analytics/equity-history/refresh")
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbols"] == 2 and body["total_bars"] == 5


def test_refresh_surfaces_nse_failure_as_502(client: TestClient):
    from slbm_tracker.services.nse_client import NSEConnectionError

    with mock.patch(
        "slbm_tracker.api.routes.analytics.EquityHistoryService.refresh_symbol",
        new=mock.AsyncMock(side_effect=NSEConnectionError("network down")),
    ):
        resp = client.post("/api/analytics/equity-history/refresh?symbol=RELIANCE")
    assert resp.status_code == 502
    assert "NSE fetch failed" in resp.json()["detail"]
