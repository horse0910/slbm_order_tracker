"""Phase 3.3/3.5/3.7: opportunities at the live Series B bid, net of charges.

On the real IRFC quote (1 Oct 2026 close; spot 77.06), 1,000 shares held:
XO (bid 0.05 x 2,069, 5 days) nets 2.17% p.a. and XN (0.35 x 10,850, 33 days)
3.62%: both below the 4% floor. XD (1.17 x 1,376, 61 days) nets 6.82%:
actionable, ticket 'IRFC | lend 1000 | XD | ₹1.17/share'.
"""

from __future__ import annotations

import datetime
import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from slbm_tracker.db.models import SLBSnapshot, Stock
from slbm_tracker.services import opportunity_service
from slbm_tracker.services.nse_service import NSEService

FIXTURES = Path(__file__).parent / "fixtures" / "nse"
TODAY = datetime.date(2026, 10, 1)
SWEPT = datetime.datetime(2026, 10, 1, 11, 0)  # 16:30 IST
NOW = SWEPT + datetime.timedelta(minutes=30)


@pytest.fixture()
async def irfc(db: Session) -> Stock:
    stock = Stock(symbol="IRFC", quantity=1000)
    db.add(stock)
    db.commit()
    client = AsyncMock()
    client.fetch_slb_quote.return_value = json.loads((FIXTURES / "getquote_slbm_IRFC.json").read_text())
    await NSEService(db, client=client).get_slb_quote("IRFC", today=TODAY, now=SWEPT)
    return stock


def _options(db: Session, now=NOW, **kw):
    board = opportunity_service.build(db, today=TODAY, now=now, **kw)
    return {o["series_code"]: o for s in board["stocks"] for o in s["options"]}, board


async def test_irfc_next_three_series_b_expiries_priced_at_the_bid(db: Session, irfc):
    opts, _ = _options(db)
    assert list(opts) == ["XO", "XN", "XD"]
    xd = opts["XD"]
    assert (xd["best_bid"], xd["best_bid_qty"], xd["lendable"], xd["fillable"]) == (1.17, 1376, 1000, 1000)
    assert xd["days"] == 61
    assert (xd["gross_fee"], xd["net_fee"]) == (Decimal("1170.00"), Decimal("878.54"))
    assert xd["net_yield_pa"] == Decimal("6.82")
    assert xd["actionable"] is True and xd["reasons"] == []
    assert xd["ticket"] == "IRFC | lend 1000 | XD | ₹1.17/share"


async def test_below_the_floor_lists_the_reason_and_has_no_ticket(db: Session, irfc):
    opts, _ = _options(db)
    xn = opts["XN"]
    assert xn["net_yield_pa"] == Decimal("3.62") and xn["actionable"] is False
    assert xn["reasons"] == ["net yield 3.62% is below the 4.00% floor"]
    assert xn["ticket"] is None
    # XO's own underlying is 0 on NSE; the spot falls back to IRFC's last price (77.06).
    assert opts["XO"]["spot"] == Decimal("77.06")


async def test_series_a_and_special_series_never_appear(db: Session, irfc):
    opts, board = _options(db, expiries=6)
    assert set(opts) <= {"XO", "XN", "XD", "X1", "X2", "X3", "X4", "X5", "X6", "X7", "X8", "X9"}
    assert len(opts) == 6


async def test_a_quote_older_than_24_hours_is_never_actionable(db: Session, irfc):
    opts, _ = _options(db, now=SWEPT + datetime.timedelta(hours=24, seconds=1))
    assert opts["XD"]["actionable"] is False
    assert "quote older than 24 hours" in opts["XD"]["reasons"]


async def test_pledged_shares_shrink_the_fill(db: Session, irfc):
    irfc.pledged_quantity = 900
    db.commit()
    opts, _ = _options(db)
    assert (opts["XD"]["lendable"], opts["XD"]["fillable"]) == (100, 100)


def _snap(db: Session, symbol: str, **kw) -> None:
    base = dict(symbol=symbol, series="Nov-2026", series_code="XN", series_family="B",
                expiry_date=datetime.date(2026, 11, 3), settlement_date=datetime.date(2026, 11, 3),
                best_bid=0.30, best_bid_qty=1000, best_ask=0.5, best_ask_qty=10, ltp=0.3,
                underlying_price=1200.0, volume=0, open_positions=0, timestamp=SWEPT, source="NSE_QUOTE")
    base.update(kw)
    db.add(SLBSnapshot(**base))
    db.commit()


def test_small_parcel_where_charges_exceed_the_fee(db: Session):
    db.add(Stock(symbol="PRICEY", quantity=50))
    _snap(db, "PRICEY")
    opts, _ = _options(db)
    xn = opts["XN"]
    assert xn["net_fee"] == Decimal("-3.88")
    assert xn["actionable"] is False
    assert "charges exceed the fee" in xn["reasons"]


def test_no_live_bid_and_minimum_fill(db: Session):
    db.add_all([Stock(symbol="NOBID", quantity=100), Stock(symbol="TINY", quantity=5)])
    _snap(db, "NOBID", best_bid=0.0, best_bid_qty=0)
    _snap(db, "TINY", best_bid=50.0, best_bid_qty=1000)
    opts = {s["symbol"]: s["options"][0] for s in opportunity_service.build(db, today=TODAY, now=NOW, min_fill_qty=10)["stocks"]}
    assert "no live bid" in opts["NOBID"]["reasons"]
    assert opts["TINY"]["fillable"] == 5
    assert "fillable 5 is below the minimum of 10" in opts["TINY"]["reasons"]


def test_corporate_action_inside_the_lending_period_blocks(db: Session):
    db.add(Stock(symbol="DIVCO", quantity=1000))
    raw = json.dumps({"corporate_action": "DIVIDEND - RS 5", "corporate_action_ex_date": "2026-10-20"})
    _snap(db, "DIVCO", best_bid=20.0, raw_data=raw)
    opts, _ = _options(db)
    assert opts["XN"]["actionable"] is False
    assert "corporate action: DIVIDEND - RS 5 (ex 2026-10-20)" in opts["XN"]["reasons"]


def test_corporate_action_already_past_does_not_block(db: Session):
    db.add(Stock(symbol="OLDDIV", quantity=1000))
    raw = json.dumps({"corporate_action": "DIVIDEND - RS 5", "corporate_action_ex_date": "2026-03-13"})
    _snap(db, "OLDDIV", best_bid=20.0, raw_data=raw)
    opts, _ = _options(db)
    assert opts["XN"]["actionable"] is True


async def test_quote_service_keeps_the_ex_date(db: Session, irfc):
    snap = db.query(SLBSnapshot).filter_by(symbol="IRFC", series_code="XD").one()
    assert json.loads(snap.raw_data)["corporate_action_ex_date"] == "2026-03-13"


async def test_api_returns_stocks_across_expiries(client: TestClient, db: Session, irfc, monkeypatch):
    monkeypatch.setattr(opportunity_service, "_utcnow", lambda: NOW)
    monkeypatch.setattr(opportunity_service, "today_ist", lambda: TODAY)
    body = client.get("/api/opportunities", params={"expiries": 2}).json()
    assert body["expiries"] == 2 and body["min_net_yield_pct"] == 4.0
    irfc_row = body["stocks"][0]
    assert irfc_row["symbol"] == "IRFC" and [o["series_code"] for o in irfc_row["options"]] == ["XO", "XN"]
    assert irfc_row["options"][1]["net_yield_pa"] == 3.62
    assert client.get("/api/opportunities", params={"expiries": 7}).status_code == 422


# --- Fix 1b: days to settlement and net ₹ beside every net yield; min net fee --


async def test_every_option_carries_days_to_settlement_and_net_rupees(db: Session, irfc):
    opts, _ = _options(db)
    assert [(c, o["days_to_settlement"], o["net_fee"]) for c, o in opts.items()] == [
        ("XO", 5, Decimal("22.86")), ("XN", 33, Decimal("252.06")), ("XD", 61, Decimal("878.54")),
    ]


async def test_min_net_fee_is_off_by_default_and_blocks_when_set(db: Session, irfc):
    opts, _ = _options(db)
    assert opts["XD"]["actionable"] is True
    opts, _ = _options(db, min_net_fee_inr=1000)
    assert opts["XD"]["actionable"] is False
    assert "net fee below ₹1,000.00" in opts["XD"]["reasons"]


async def test_dashboard_card_shows_days_and_net_rupees(db: Session, irfc, monkeypatch):
    from slbm_tracker.services.dashboard_service import DashboardService

    monkeypatch.setattr(opportunity_service, "_utcnow", lambda: NOW)
    best = DashboardService.build_summary(db, today=TODAY)["best_opportunities"]
    assert [(o["series"], o["days_to_settlement"], o["net_fee"]) for o in best] == [("XD", 61, 878.54)]


def test_min_net_fee_setting_is_editable(client: TestClient):
    assert client.get("/api/settings").json()["min_net_fee_inr"] == 0.0
    assert client.put("/api/settings", json={"min_net_fee_inr": 250}).json()["min_net_fee_inr"] == 250.0
    assert client.put("/api/settings", json={"min_net_fee_inr": -1}).status_code == 422
