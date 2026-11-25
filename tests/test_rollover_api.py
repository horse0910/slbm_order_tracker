"""API tests for the rollover decision-support endpoint."""

from __future__ import annotations

import datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from slbm_tracker.db.models import LendingContract, SLBSnapshot, Stock


def _seed_candidate_with_future(db: Session):
    """A near-expiry contract + one persisted future-series snapshot."""
    stock = Stock(symbol="RELIANCE", quantity=1000, avg_buy_price=2500.0)
    db.add(stock)
    db.commit()
    db.refresh(stock)

    # Expiry far enough out that it is always a rollover candidate relative to
    # "today" at request time is not deterministic; instead place expiry in the
    # past-but-open window so it is a candidate regardless of the machine clock.
    contract = LendingContract(
        stock_id=stock.id,
        series="Nov-2026",
        expiry_date=datetime.date(2020, 1, 7),  # long past -> candidate (open, past expiry)
        quantity_lent=100,
        lending_fee=5.0,
        status="ACTIVE",
        trade_date=datetime.date(2019, 12, 1),
    )
    db.add(contract)
    db.commit()

    snap = SLBSnapshot(series_family="B", series_code="X2", best_bid=6.5, best_bid_qty=5000,
        underlying_price=2500.0,
        symbol="RELIANCE",
        series="Feb-2020",
        expiry_date=datetime.date(2020, 2, 4),
        ltp=6.5,
        annualized_yield=9.4,
        open_positions=5000,
        timestamp=datetime.datetime(2020, 1, 3, 10, 0, 0),
    )
    db.add(snap)
    db.commit()
    return stock, contract


def test_rollover_endpoint_returns_recommendations(client: TestClient, db: Session):
    _seed_candidate_with_future(db)
    res = client.get("/api/rollover")
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body) == 1
    rec = body[0]
    assert rec["symbol"] == "RELIANCE"
    assert rec["status"] == "ROLL_AVAILABLE"
    assert rec["best_option"]["series"] == "Feb-2020"
    assert rec["favorable"] is True
    assert round(rec["fee_delta"], 4) == 1.5


def test_rollover_endpoint_empty_when_no_candidates(client: TestClient, db: Session):
    # A stock with no contracts -> nothing to advise on.
    stock = Stock(symbol="TCS", quantity=100, avg_buy_price=3200.0)
    db.add(stock)
    db.commit()
    res = client.get("/api/rollover")
    assert res.status_code == 200
    assert res.json() == []


def test_rollover_endpoint_reports_bid_pricing_and_days_used(client: TestClient, db: Session):
    _seed_candidate_with_future(db)
    rec = client.get("/api/rollover").json()[0]
    opt = rec["best_option"]
    assert (opt["series_code"], opt["best_bid"], opt["fillable"], opt["within_cap"]) == ("X2", 6.5, 100, True)
    assert opt["net_fee"] == 481.26 and opt["chain_days"] == 65
    assert rec["origin_date"] == "2019-12-01"
    assert rec["days_used_label"].endswith("of 365 days used")
