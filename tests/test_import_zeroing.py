"""Phase 1.6: a Zerodha import zeroes stocks missing from the file.

The stock is never deleted (its contract history stays). It records
holdings_imported_at, so in-demat arithmetic (house rule 5) knows which
contracts the file already reflects. A zeroed stock is no longer polled or
offered unless it is under an ACTIVE contract (lent shares are left out of
Zerodha's export).
"""

from __future__ import annotations

import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from slbm_tracker.db.models import LendingContract, SLBSnapshot, Stock
from slbm_tracker.services.dashboard_service import DashboardService
from slbm_tracker.services.portfolio_service import PortfolioService

# 1 Oct 2026, 19:00 IST in naive UTC.
NOW = datetime.datetime(2026, 10, 1, 13, 30)

KITE_HEADER = "Symbol,ISIN,Quantity Available,Total Quantity,Average Price\n"


def _csv(*rows: tuple[str, int]) -> str:
    return KITE_HEADER + "".join(f"NSE:{s},,{q},{q},100.00\n" for s, q in rows)


def _seed(db: Session) -> dict[str, Stock]:
    stocks = {s: Stock(symbol=s, quantity=q) for s, q in (("SBIN", 500), ("INFY", 50), ("IRFC", 1000))}
    db.add_all(stocks.values())
    db.commit()
    return stocks


def _lend(db: Session, stock: Stock, qty: int, trade_date=datetime.date(2026, 9, 29)) -> None:
    db.add(LendingContract(stock_id=stock.id, series="XN", expiry_date=datetime.date(2026, 11, 3),
                           quantity_lent=qty, lending_fee=1.0, status="ACTIVE", trade_date=trade_date))
    db.commit()


def test_missing_stock_is_zeroed_kept_and_no_longer_polled(db: Session):
    stocks = _seed(db)
    db.add(LendingContract(stock_id=stocks["SBIN"].id, series="XN", expiry_date=datetime.date(2026, 9, 1),
                           quantity_lent=100, lending_fee=1.0, status="CLOSED",
                           trade_date=datetime.date(2026, 8, 3)))
    db.commit()

    result = PortfolioService.import_zerodha_holdings(db, _csv(("INFY", 60), ("IRFC", 1000)), now=NOW)

    assert result["zeroed"] == ["SBIN"]
    assert result["zeroing_skipped"] is None
    sbin = db.query(Stock).filter_by(symbol="SBIN").one()
    assert sbin.quantity == 0
    assert db.query(LendingContract).filter_by(stock_id=sbin.id).count() == 1
    assert "SBIN" not in PortfolioService.polled_symbols(db)
    assert sorted(PortfolioService.polled_symbols(db)) == ["INFY", "IRFC"]


def test_zeroed_stock_under_active_contract_stays_polled_and_shows_as_lent(db: Session):
    stocks = _seed(db)
    _lend(db, stocks["SBIN"], 500)  # fully lent on 29 Sep, so missing from the 1 Oct export

    result = PortfolioService.import_zerodha_holdings(db, _csv(("INFY", 50), ("IRFC", 1000)), now=NOW)

    assert result["zeroed"] == ["SBIN"]
    assert "SBIN" in PortfolioService.polled_symbols(db)
    pos = PortfolioService.holding_position(db, db.query(Stock).filter_by(symbol="SBIN").one())
    assert (pos.in_demat, pos.lent, pos.total_owned) == (0, 500, 500)


def test_import_records_holdings_imported_at_on_every_stock_it_sets(db: Session):
    _seed(db)
    PortfolioService.import_zerodha_holdings(db, _csv(("INFY", 50), ("NEWCO", 7)), now=NOW)
    stamps = {s.symbol: s.holdings_imported_at for s in db.query(Stock).all()}
    assert stamps == {"SBIN": NOW, "INFY": NOW, "IRFC": NOW, "NEWCO": NOW}


def test_reimporting_a_zeroed_stock_restores_it(db: Session):
    _seed(db)
    PortfolioService.import_zerodha_holdings(db, _csv(("INFY", 50), ("IRFC", 1000)), now=NOW)
    result = PortfolioService.import_zerodha_holdings(
        db, _csv(("SBIN", 200), ("INFY", 50), ("IRFC", 1000)), now=NOW + datetime.timedelta(days=1)
    )
    assert result["zeroed"] == []
    assert db.query(Stock).filter_by(symbol="SBIN").one().quantity == 200


@pytest.mark.parametrize(
    "content",
    ["", "not,a,holdings,file\n1,2,3,4\n", KITE_HEADER],
)
def test_file_with_no_holdings_zeroes_nothing(db: Session, content: str):
    _seed(db)
    result = PortfolioService.import_zerodha_holdings(db, content, now=NOW)
    assert result["zeroed"] == []
    assert result["zeroing_skipped"]
    assert {s.symbol: s.quantity for s in db.query(Stock).all()} == {"SBIN": 500, "INFY": 50, "IRFC": 1000}


def test_unreadable_row_blocks_zeroing_but_readable_rows_still_update(db: Session):
    _seed(db)
    content = _csv(("INFY", 60)) + "NSE:IRFC,,abc,abc,1.00\n"
    result = PortfolioService.import_zerodha_holdings(db, content, now=NOW)
    assert result["zeroed"] == []
    assert "could not be read" in result["zeroing_skipped"]
    qty = {s.symbol: s.quantity for s in db.query(Stock).all()}
    assert qty == {"SBIN": 500, "INFY": 60, "IRFC": 1000}


def test_opportunities_skip_zeroed_and_fully_lent_stocks(db: Session):
    # Since Phase 3.3 opportunities are priced on lendable shares: a sold stock
    # has none, and a fully lent one has nothing left in demat to lend.
    from slbm_tracker.services import opportunity_service

    stocks = _seed(db)
    stocks["INFY"].quantity = 0  # sold
    stocks["IRFC"].quantity = 0  # fully lent
    db.commit()
    _lend(db, stocks["IRFC"], 1000)
    for sym in ("SBIN", "INFY", "IRFC"):
        db.add(SLBSnapshot(series_family="B", series_code="XN", symbol=sym, series="Nov-2026",
                           expiry_date=datetime.date(2026, 11, 3), best_bid=1.0, best_bid_qty=100,
                           underlying_price=50.0, annualized_yield=5.0, timestamp=NOW))
    db.commit()
    board = opportunity_service.build(db, today=datetime.date(2026, 10, 1), now=NOW)
    assert [s["symbol"] for s in board["stocks"]] == ["SBIN"]


def test_import_api_reports_zeroed(client: TestClient, db: Session):
    _seed(db)
    files = {"file": ("holdings.csv", _csv(("INFY", 50), ("IRFC", 1000)), "text/csv")}
    res = client.post("/api/portfolio/import", files=files)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["zeroed"] == ["SBIN"]
    assert body["zeroing_skipped"] is None
