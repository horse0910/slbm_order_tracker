"""Phase 2.13: GET /api/portfolio/live and /api/portfolio/live/trades."""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from slbm_tracker.db.models import LendingContract, Stock
from slbm_tracker.services import live_board_service
from slbm_tracker.services.nse_service import NSEService

FIXTURES = Path(__file__).parent / "fixtures" / "nse"
TODAY = datetime.date(2026, 10, 5)
T0 = datetime.datetime(2026, 10, 5, 4, 0)   # 09:30 IST
T1 = T0 + datetime.timedelta(minutes=3)


async def _sweep(db: Session, fixture: str, at: datetime.datetime, symbol: str = "IRFC") -> None:
    payload = json.loads((FIXTURES / fixture).read_text())
    for row in payload["data"]:
        row["symbol"] = symbol
    client = AsyncMock()
    client.fetch_slb_quote.return_value = payload
    await NSEService(db, client=client).get_slb_quote(symbol, today=TODAY, now=at)


@pytest.fixture()
async def board(db: Session, monkeypatch):
    db.add_all([
        Stock(symbol="IRFC", quantity=1000, is_slb_eligible=True),
        Stock(symbol="NOTSLB", quantity=5, is_slb_eligible=False),
        Stock(symbol="NEWCO", quantity=7, is_slb_eligible=True),
        Stock(symbol="SOLD", quantity=0, is_slb_eligible=True),
    ])
    # Fully lent on 1 Oct, so missing from the 2 Oct export (quantity 0).
    lent = Stock(symbol="LENTALL", quantity=0, is_slb_eligible=True,
                 holdings_imported_at=datetime.datetime(2026, 10, 2, 4, 0))
    db.add(lent)
    db.flush()
    db.add(LendingContract(stock_id=lent.id, series="XN", expiry_date=datetime.date(2026, 11, 3),
                           quantity_lent=300, lending_fee=1.0, status="ACTIVE", trade_date=datetime.date(2026, 10, 1)))
    db.commit()
    await _sweep(db, "getquote_slbm_IRFC_t0_synthetic.json", T0)
    await _sweep(db, "getquote_slbm_IRFC_t1_synthetic.json", T1)
    # Board time: 2 minutes after the last sweep (today in IST).
    monkeypatch.setattr(live_board_service, "_utcnow", lambda: T1 + datetime.timedelta(minutes=2))
    return db


def test_live_lists_every_holding_with_series_b_first(client: TestClient, board):
    body = client.get("/api/portfolio/live").json()
    symbols = [h["symbol"] for h in body["holdings"]]
    assert symbols == ["IRFC", "LENTALL", "NEWCO", "NOTSLB"]  # not-on-SLB last; SOLD absent
    irfc = body["holdings"][0]
    assert irfc["status"] == "live" and irfc["total_owned"] == 1000
    families = [s["series_family"] for s in irfc["series"]]
    assert families == sorted(families, key=["B", "A", "SPECIAL"].index)
    xn = next(s for s in irfc["series"] if s["series_code"] == "XN")
    assert (xn["ltp"], xn["traded_quantity"], xn["open_positions"]) == (0.33, 1500, 14820912)
    assert (xn["best_bid"], xn["best_bid_qty"], xn["best_ask"], xn["best_ask_qty"]) == (0.35, 10850, 0.7, 2000)
    assert xn["settlement_date"] == "2026-11-03" and xn["annualized_yield"] == 4.89
    assert xn["age_seconds"] == 120
    assert len([s for s in irfc["series"] if s["series"] == "Nov-2026"]) == 2  # 11 and XN, separate


def test_not_on_slb_lent_only_and_no_data_holdings(client: TestClient, board):
    rows = {h["symbol"]: h for h in client.get("/api/portfolio/live").json()["holdings"]}
    assert rows["NOTSLB"]["status"] == "not_on_slb" and rows["NOTSLB"]["series"] == []
    assert rows["NEWCO"]["status"] == "no_data"
    assert rows["LENTALL"]["total_owned"] == 300  # fully lent, still on the board


def test_outside_market_hours_the_board_shows_last_values_with_their_age(client: TestClient, board, monkeypatch):
    evening = T1 + datetime.timedelta(hours=10)  # 19:33 IST
    monkeypatch.setattr(live_board_service, "_utcnow", lambda: evening)
    body = client.get("/api/portfolio/live").json()
    assert body["market_open"] is False
    xn = next(s for s in body["holdings"][0]["series"] if s["series_code"] == "XN")
    assert xn["ltp"] == 0.33 and xn["age_seconds"] == 36000


def test_board_reports_a_sweep_that_cannot_finish_in_time(client: TestClient, db: Session, monkeypatch):
    for i in range(100):
        db.add(Stock(symbol=f"S{i:03d}", quantity=1))
    db.commit()
    body = client.get("/api/portfolio/live").json()
    assert "100 symbols" in body["sweep_warning"]


def test_trades_feed_since(client: TestClient, board):
    body = client.get("/api/portfolio/live/trades").json()  # default: today (IST)
    assert len(body["events"]) == 1
    e = body["events"][0]
    assert (e["symbol"], e["series_code"], e["quantity"], e["last_fee"]) == ("IRFC", "XN", 500, 0.33)
    assert e["inferred"] is True
    later = (T1 + datetime.timedelta(seconds=1)).isoformat()
    assert client.get("/api/portfolio/live/trades", params={"since": later}).json()["events"] == []


def test_portfolio_by_id_still_works(client: TestClient, board):
    stock_id = board.query(Stock).filter_by(symbol="IRFC").one().id
    assert client.get(f"/api/portfolio/{stock_id}").json()["symbol"] == "IRFC"
