"""Phase 3.2: pledged shares cannot be lent. Lendable = in demat − pledged.

Zerodha's header for pledged shares is matched with T's kite.py aliases
(collateral quantity, pledged quantity, pledged qty). No real Zerodha export
with that column is in the fixtures yet; these CSVs are hand-made for the
parser, not NSE data.
"""

from __future__ import annotations

import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from slbm_tracker.db.models import LendingContract, Stock
from slbm_tracker.services.portfolio_service import PortfolioService
from slbm_tracker.utils.zerodha_parser import ZerodhaHoldingsParser

NOW = datetime.datetime(2026, 10, 5, 4, 0)


@pytest.mark.parametrize("header", ["Collateral Quantity", "Pledged Quantity", "Pledged Qty", "collateral_quantity"])
def test_parser_reads_the_pledged_column(header):
    csv_text = f"Symbol,{header},Total Quantity,Average Price\nNSE:SBIN,100,250,800\nNSE:INFY,,10,1500\n"
    holdings, errors = ZerodhaHoldingsParser.parse_csv(csv_text)
    assert errors == []
    by = {h.symbol: h for h in holdings}
    assert (by["SBIN"].quantity, by["SBIN"].pledged_quantity) == (250, 100)
    assert by["INFY"].pledged_quantity == 0


def test_pledged_column_is_never_taken_for_the_quantity():
    holdings, _ = ZerodhaHoldingsParser.parse_csv("Instrument,Collateral Quantity,Qty.\nSBIN,100,250\n")
    assert (holdings[0].quantity, holdings[0].pledged_quantity) == (250, 100)


def test_no_pledged_column_means_zero_pledged():
    holdings, _ = ZerodhaHoldingsParser.parse_csv("Instrument,Qty.,Avg. cost\nSBIN,250,800\n")
    assert holdings[0].pledged_quantity == 0


def test_250_held_with_100_pledged_gives_150_lendable(db: Session):
    PortfolioService.import_zerodha_holdings(
        db, "Symbol,Collateral Quantity,Total Quantity\nNSE:SBIN,100,250\n", now=NOW
    )
    stock = db.query(Stock).filter_by(symbol="SBIN").one()
    assert stock.pledged_quantity == 100
    pos = PortfolioService.holding_position(db, stock)
    assert (pos.in_demat, pos.pledged, pos.lendable) == (250, 100, 150)


def test_lendable_subtracts_same_day_lending_and_never_goes_negative(db: Session):
    stock = Stock(symbol="SBIN", quantity=250, pledged_quantity=100, holdings_imported_at=NOW)
    db.add(stock)
    db.flush()
    db.add(LendingContract(stock_id=stock.id, expiry_date=datetime.date(2026, 11, 3), quantity_lent=200,
                           lending_fee=1.0, status="ACTIVE", trade_date=datetime.date(2026, 10, 5)))
    db.commit()
    pos = PortfolioService.holding_position(db, stock)
    assert (pos.in_demat, pos.pledged, pos.lendable) == (50, 100, 0)


def test_zeroed_stock_has_nothing_pledged(db: Session):
    db.add(Stock(symbol="SOLD", quantity=40, pledged_quantity=40))
    db.commit()
    PortfolioService.import_zerodha_holdings(db, "Symbol,Total Quantity\nNSE:INFY,10\n", now=NOW)
    assert db.query(Stock).filter_by(symbol="SOLD").one().pledged_quantity == 0


def test_portfolio_api_shows_pledged_and_lendable(client: TestClient, db: Session):
    db.add(Stock(symbol="SBIN", quantity=250, pledged_quantity=100))
    db.commit()
    row = client.get("/api/portfolio").json()[0]
    assert (row["pledged_quantity"], row["lendable"]) == (100, 150)


def test_contract_quantity_check_excludes_pledged_shares(db: Session):
    from slbm_tracker.services.contract_service import ContractService

    stock = Stock(symbol="SBIN", quantity=250, pledged_quantity=100, holdings_imported_at=NOW)
    db.add(stock)
    db.commit()
    kwargs = dict(db=db, stock_id=stock.id, expiry_date=datetime.date(2026, 11, 3),
                  lending_fee=1.0, trade_date=datetime.date(2026, 10, 5))
    with pytest.raises(ValueError, match="100 pledged"):
        ContractService.create_contract(quantity_lent=151, **kwargs)
    ContractService.create_contract(quantity_lent=150, **kwargs)
