"""Phase 2.11: GET /api/slb/quote/{symbol} and /api/slb/quote/{symbol}/history."""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from slbm_tracker.api.routes.slb import get_nse_service
from slbm_tracker.services.nse_client import NSEBlockedError, NSEConnectionError
from slbm_tracker.services.nse_service import NSEService

FIXTURES = Path(__file__).parent / "fixtures" / "nse"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture()
def nse(client: TestClient, db: Session):
    mock = AsyncMock()
    mock.fetch_slb_quote.return_value = _load("getquote_slbm_IRFC.json")
    mock.fetch_slb_by_symbol.return_value = _load("live_analysis_slb_symbol_IRFC.json")
    mock.fetch_slb_history.return_value = _load("slb_history_IRFC.json")
    client.app.dependency_overrides[get_nse_service] = lambda: NSEService(db=db, client=mock)
    yield mock
    client.app.dependency_overrides.pop(get_nse_service, None)


def test_quote_returns_every_series_with_code_and_family(client: TestClient, nse):
    res = client.get("/api/slb/quote/irfc")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["symbol"] == "IRFC" and body["source"] == "quote"
    assert body["as_of"] == "01-Oct-2026 17:00:00"
    xn = next(s for s in body["series"] if s["series_code"] == "XN")
    assert xn["series_family"] == "B" and xn["settlement_date"] == "2026-11-03"
    assert (xn["best_bid"], xn["best_bid_qty"]) == (0.35, 10850)
    assert {"11", "XN"} <= {s["series_code"] for s in body["series"] if s["month"] == "Nov-2026"}


def test_quote_transport_error_falls_back_to_symbol_view_not_500(client: TestClient, nse):
    nse.fetch_slb_quote.side_effect = NSEConnectionError("timed out")
    res = client.get("/api/slb/quote/IRFC")
    assert res.status_code == 200, res.text
    assert res.json()["source"] == "symbol_view"
    assert len(res.json()["series"]) == 14


def test_quote_when_both_calls_fail_serves_the_cache_not_500(client: TestClient, nse):
    nse.fetch_slb_quote.side_effect = NSEConnectionError("timed out")
    nse.fetch_slb_by_symbol.side_effect = NSEConnectionError("timed out")
    res = client.get("/api/slb/quote/IRFC")
    assert res.status_code == 200, res.text
    assert res.json()["series"] == []


def test_quote_block_serves_cache_without_a_second_call(client: TestClient, nse):
    client.get("/api/slb/quote/IRFC")  # persist rows first
    nse.fetch_slb_quote.side_effect = NSEBlockedError("403")
    res = client.get("/api/slb/quote/IRFC")
    assert res.status_code == 200
    assert res.json()["source"] == "cache" and res.json()["blocked"] is True
    nse.fetch_slb_by_symbol.assert_not_called()


def test_history_passes_dates_and_returns_parsed_rows(client: TestClient, nse):
    res = client.get("/api/slb/quote/IRFC/history", params={"from": "2026-09-01", "to": "2026-10-04"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["from"], body["to"]) == ("2026-09-01", "2026-10-04")
    assert len(body["rows"]) == 70
    assert nse.fetch_slb_history.await_args.args == ("IRFC", datetime.date(2026, 9, 1), datetime.date(2026, 10, 4))


def test_history_defaults_to_the_last_30_days_ist(client: TestClient, nse, monkeypatch):
    from slbm_tracker.api.routes import slb as slb_routes

    monkeypatch.setattr(slb_routes, "today_ist", lambda: datetime.date(2026, 10, 4))
    res = client.get("/api/slb/quote/IRFC/history")
    assert (res.json()["from"], res.json()["to"]) == ("2026-09-04", "2026-10-04")


def test_history_rejects_a_reversed_range(client: TestClient, nse):
    res = client.get("/api/slb/quote/IRFC/history", params={"from": "2026-10-05", "to": "2026-10-04"})
    assert res.status_code == 422


def test_history_nse_failure_is_502_not_500(client: TestClient, nse):
    nse.fetch_slb_history.side_effect = NSEConnectionError("down")
    res = client.get("/api/slb/quote/IRFC/history")
    assert res.status_code == 502
