"""Phase 1.7: lent shares on the holdings page and the dashboard.

Zerodha's export leaves lent shares out, so the app shows them from
contracts: per stock in demat / lent out / total owned, a Lent badge per
ACTIVE contract (series, settlement date), and a dashboard Lent out card.
"""

from __future__ import annotations

import datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from slbm_tracker.db.models import LendingContract, SLBSnapshot, Stock, StockPriceHistory
from slbm_tracker.services.dashboard_service import DashboardService
from slbm_tracker.services.portfolio_service import PortfolioService

KITE_HEADER = "Symbol,ISIN,Quantity Available,Total Quantity,Average Price\n"
T = datetime.date(2026, 10, 1)
EVENING_T = datetime.datetime(2026, 10, 1, 13, 30)  # 19:00 IST on T, naive UTC
MORNING_T1 = datetime.datetime(2026, 10, 2, 4, 0)   # 09:30 IST on T+1


def _csv(*rows: tuple[str, int]) -> str:
    return KITE_HEADER + "".join(f"NSE:{s},,{q},{q},100.00\n" for s, q in rows)


def _lend(db: Session, stock_id: int, qty: int, *, trade_date=T, fee=1.5,
          series="XN", expiry=datetime.date(2026, 11, 3), reverse_leg=None) -> None:
    db.add(LendingContract(stock_id=stock_id, series=series, expiry_date=expiry,
                           reverse_leg_date=reverse_leg, quantity_lent=qty, lending_fee=fee,
                           status="ACTIVE", trade_date=trade_date))
    db.commit()


def _row(client: TestClient, symbol: str) -> dict:
    rows = client.get("/api/portfolio").json()
    return next(r for r in rows if r["symbol"] == symbol)


def test_holdings_show_in_demat_lent_and_total_across_imports(client: TestClient, db: Session):
    # 500 imported on the morning of T, then 200 lent on T.
    PortfolioService.import_zerodha_holdings(db, _csv(("SBIN", 500)), now=datetime.datetime(2026, 10, 1, 4, 0))
    sbin = db.query(Stock).filter_by(symbol="SBIN").one()
    _lend(db, sbin.id, 200, reverse_leg=datetime.date(2026, 11, 3))

    row = _row(client, "SBIN")
    assert (row["in_demat"], row["lent"], row["total_owned"]) == (300, 200, 500)
    assert row["lent_contracts"] == [
        {"contract_id": row["lent_contracts"][0]["contract_id"], "series": "XN",
         "quantity_lent": 200, "trade_date": "2026-10-01", "settlement_date": "2026-11-03"}
    ]

    # Re-import on T+1 showing 300: unchanged 300 / 200 / 500.
    PortfolioService.import_zerodha_holdings(db, _csv(("SBIN", 300)), now=MORNING_T1)
    row = _row(client, "SBIN")
    assert (row["in_demat"], row["lent"], row["total_owned"]) == (300, 200, 500)


def test_fully_lent_stock_missing_from_the_file_is_still_listed(client: TestClient, db: Session):
    PortfolioService.import_zerodha_holdings(db, _csv(("SBIN", 500), ("IRFC", 1000)), now=EVENING_T)
    irfc = db.query(Stock).filter_by(symbol="IRFC").one()
    _lend(db, irfc.id, 1000, trade_date=T)
    PortfolioService.import_zerodha_holdings(db, _csv(("SBIN", 500)), now=MORNING_T1)

    row = _row(client, "IRFC")
    assert row["quantity"] == 0
    assert (row["in_demat"], row["lent"], row["total_owned"]) == (0, 1000, 1000)
    assert row["lent_contracts"][0]["series"] == "XN"


def test_settlement_date_falls_back_to_expiry(client: TestClient, db: Session):
    db.add(Stock(symbol="INFY", quantity=100))
    db.commit()
    stock = db.query(Stock).filter_by(symbol="INFY").one()
    _lend(db, stock.id, 40, series="XD", expiry=datetime.date(2026, 12, 1))
    row = client.get(f"/api/portfolio/{stock.id}").json()
    assert (row["in_demat"], row["lent"], row["total_owned"]) == (60, 40, 100)
    assert row["lent_contracts"][0]["settlement_date"] == "2026-12-01"


def test_dashboard_lent_out_card(db: Session):
    a = Stock(symbol="SBIN", quantity=300)
    b = Stock(symbol="IRFC", quantity=0)
    c = Stock(symbol="INFY", quantity=50)
    d = Stock(symbol="NOPRICE", quantity=0)
    db.add_all([a, b, c, d])
    db.commit()
    _lend(db, a.id, 200, fee=1.5)                 # gross 300.00, net 213.86
    _lend(db, b.id, 1000, fee=0.31)               # gross 310.00, net 221.50
    _lend(db, d.id, 10, fee=2.0)                  # gross 20.00, net -0.06
    db.add(LendingContract(stock_id=c.id, series="XN", expiry_date=datetime.date(2026, 11, 3),
                           quantity_lent=50, lending_fee=9.0, status="CLOSED", trade_date=T))
    # SBIN priced from the latest snapshot's underlying price; IRFC from the
    # stored daily close; NOPRICE has neither and is reported, not guessed.
    ts = datetime.datetime(2026, 10, 1, 9, 0)
    db.add(SLBSnapshot(symbol="SBIN", series="XN", expiry_date=datetime.date(2026, 11, 3),
                       underlying_price=800.0, timestamp=ts - datetime.timedelta(hours=1)))
    db.add(SLBSnapshot(symbol="SBIN", series="XD", expiry_date=datetime.date(2026, 12, 1),
                       underlying_price=812.5, timestamp=ts))
    db.add(StockPriceHistory(symbol="IRFC", date=datetime.date(2026, 9, 30), close=120.25))
    db.commit()

    lent_out = DashboardService.build_summary(db, today=T)["lent_out"]

    assert lent_out["stocks"] == 3
    assert lent_out["shares"] == 1210
    assert lent_out["value_at_last_price"] == 282750.00  # 200×812.50 + 1000×120.25
    assert lent_out["unpriced_symbols"] == ["NOPRICE"]
    assert lent_out["fee_income_due_gross"] == 630.00
    assert lent_out["fee_income_due_net"] == 435.30  # 213.86 + 221.50 − 0.06


def test_dashboard_lent_out_is_empty_without_active_contracts(client: TestClient):
    body = client.get("/api/dashboard/summary").json()
    assert body["lent_out"] == {
        "stocks": 0, "shares": 0, "value_at_last_price": 0.0, "unpriced_symbols": [],
        "fee_income_due_gross": 0.0, "fee_income_due_net": 0.0,
    }


def test_zero_underlying_price_from_nse_means_no_price(db: Session):
    # NSE sends underLyingLtp = 0 on some series (see the IRFC fixture).
    s = Stock(symbol="IRFC", quantity=0)
    db.add(s)
    db.commit()
    _lend(db, s.id, 800, fee=0.35)
    ts = datetime.datetime(2026, 9, 30, 4, 0)
    db.add(SLBSnapshot(symbol="IRFC", series="XN", expiry_date=datetime.date(2026, 11, 3),
                       underlying_price=79.95, timestamp=ts))
    db.add(SLBSnapshot(symbol="IRFC", series="X9", expiry_date=datetime.date(2027, 9, 7),
                       underlying_price=0.0, timestamp=ts + datetime.timedelta(seconds=1)))
    db.commit()
    lent_out = DashboardService.build_summary(db, today=T)["lent_out"]
    assert lent_out["value_at_last_price"] == 63960.00  # 800 × 79.95
    assert lent_out["unpriced_symbols"] == []


def test_dashboard_shares_held_is_total_owned(db: Session):
    PortfolioService.import_zerodha_holdings(db, _csv(("SBIN", 500), ("IRFC", 1000)), now=EVENING_T)
    irfc = db.query(Stock).filter_by(symbol="IRFC").one()
    sbin = db.query(Stock).filter_by(symbol="SBIN").one()
    _lend(db, irfc.id, 1000, trade_date=T)        # fully lent, out of the next export
    _lend(db, sbin.id, 200, trade_date=datetime.date(2026, 10, 2))  # lent after that export
    PortfolioService.import_zerodha_holdings(db, _csv(("SBIN", 500)), now=MORNING_T1)

    portfolio = DashboardService.build_summary(db, today=T)["portfolio"]
    assert portfolio["total_quantity"] == 500            # imported, as before
    assert (portfolio["in_demat"], portfolio["lent"], portfolio["total_owned"]) == (300, 1200, 1500)
