"""Unit tests for Zerodha CSV import parsing and ingestion."""

from sqlalchemy.orm import Session

from slbm_tracker.services.portfolio_service import PortfolioService
from slbm_tracker.utils.zerodha_parser import ZerodhaHoldingsParser


def test_parse_zerodha_console_format(zerodha_console_csv: str):
    parsed, errors = ZerodhaHoldingsParser.parse_csv(zerodha_console_csv)
    assert len(parsed) == 3
    assert len(errors) == 0

    reliance = next(p for p in parsed if p.symbol == "RELIANCE")
    assert reliance.quantity == 50
    assert reliance.avg_buy_price == 2450.50

    infy = next(p for p in parsed if p.symbol == "INFY")
    assert infy.quantity == 120
    assert infy.avg_buy_price == 1350.00

    tcs = next(p for p in parsed if p.symbol == "TCS")
    assert tcs.quantity == 25
    assert tcs.avg_buy_price == 3200.00


def test_parse_zerodha_kite_format(zerodha_kite_csv: str):
    parsed, errors = ZerodhaHoldingsParser.parse_csv(zerodha_kite_csv)
    assert len(parsed) == 2
    assert len(errors) == 0

    hdfc = next(p for p in parsed if p.symbol == "HDFCBANK")
    assert hdfc.quantity == 75
    assert hdfc.avg_buy_price == 1520.40
    assert hdfc.isin == "INE040A01034"

    itc = next(p for p in parsed if p.symbol == "ITC")
    assert itc.quantity == 200
    assert itc.avg_buy_price == 430.15
    assert itc.isin == "INE154A01025"


def test_parse_currency_symbols_and_commas():
    raw_csv = (
        "Instrument,Qty.,Avg. cost\n"
        "BAJFINANCE,\"1,500\",\"₹6,540.50\"\n"
        "NESTLEIND,20,\"₹2,200.00\"\n"
    )
    parsed, errors = ZerodhaHoldingsParser.parse_csv(raw_csv)
    assert len(parsed) == 2
    assert parsed[0].symbol == "BAJFINANCE"
    assert parsed[0].quantity == 1500
    assert parsed[0].avg_buy_price == 6540.50


def test_import_zerodha_csv_into_database(db: Session, zerodha_console_csv: str):
    result = PortfolioService.import_zerodha_holdings(db=db, csv_content=zerodha_console_csv)
    assert result["total_parsed"] == 3
    assert result["added"] == 3
    assert result["updated"] == 0
    assert len(result["stocks"]) == 3

    # Re-importing same with updated quantity
    updated_csv = (
        "Instrument,Qty.,Avg. cost\n"
        "RELIANCE,75,2500.00\n"
        "INFY,120,1350.00\n"
    )
    re_result = PortfolioService.import_zerodha_holdings(db=db, csv_content=updated_csv)
    assert re_result["total_parsed"] == 2
    assert re_result["added"] == 0
    assert re_result["updated"] == 2

    # Verify RELIANCE has 75 now
    stock = PortfolioService.get_stock_by_symbol(db=db, symbol="RELIANCE")
    assert stock.quantity == 75
    assert stock.avg_buy_price == 2500.00


def test_parse_empty_or_invalid_csv():
    parsed, errors = ZerodhaHoldingsParser.parse_csv("")
    assert len(parsed) == 0
    assert "empty" in errors[0].lower()

    bad_csv = "RandomCol1,RandomCol2\n1,2"
    parsed, errors = ZerodhaHoldingsParser.parse_csv(bad_csv)
    assert len(parsed) == 0
    assert "could not identify required" in errors[0].lower()


def test_import_dedupes_duplicate_symbols_within_csv(db: Session):
    """MED M2: a duplicate symbol in a single CSV must NOT lose the whole batch.

    Before the fix, two rows for one symbol produced two pending INSERTs that
    collided on the unique constraint at the single commit — raising
    IntegrityError and dropping every row in the import. Rows are now deduped
    (last occurrence wins) so the import stays atomic and its counts consistent.
    """
    csv_with_dupe = (
        "Instrument,Qty.,Avg. cost\n"
        "RELIANCE,50,2450.00\n"
        "INFY,120,1350.00\n"
        "RELIANCE,75,2500.00\n"  # duplicate symbol -> later row wins
    )
    result = PortfolioService.import_zerodha_holdings(db=db, csv_content=csv_with_dupe)

    # The batch is persisted (not lost); counts stay consistent (added == parsed).
    assert result["total_parsed"] == 2
    assert result["added"] == 2
    assert result["updated"] == 0
    assert len(result["stocks"]) == 2
    assert any("duplicate symbol" in e.lower() for e in result["errors"])

    # Last occurrence wins.
    reliance = PortfolioService.get_stock_by_symbol(db=db, symbol="RELIANCE")
    assert reliance.quantity == 75
    assert reliance.avg_buy_price == 2500.00

    # Both distinct symbols survived the import.
    assert len(PortfolioService.list_stocks(db=db)) == 2


def test_unreadable_quantity_is_reported_not_treated_as_zero():
    content = (
        "Symbol,ISIN,Quantity Available,Total Quantity,Average Price\n"
        "NSE:INFY,,60,60,1.00\n"
        "NSE:IRFC,,abc,abc,1.00\n"
        "NSE:SBIN,,-,-,1.00\n"
    )
    holdings, errors = ZerodhaHoldingsParser.parse_csv(content)
    assert [h.symbol for h in holdings] == ["INFY"]
    assert len(errors) == 1 and "IRFC" in errors[0] and "Invalid quantity" in errors[0]
