"""Phase 2.3/2.4/2.9: GetQuoteApi parser on the real IRFC fixtures (1 Oct 2026 close).

The series code and family are kept (B collapsed them into the month label,
which is why IRFC Nov-2026 appeared twice in one poll). Family: two digits = A,
X1-X9/XO/XN/XD = B, anything else (R3, (O1), (ON), (OD)...) = SPECIAL.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from slbm_tracker.services.nse_parser import NSEDataParser, series_family

FIXTURES = Path(__file__).parent / "fixtures" / "nse"
TODAY = datetime.date(2026, 10, 1)


@pytest.fixture(scope="module")
def quote_rows() -> dict[str, dict]:
    raw = json.loads((FIXTURES / "getquote_slbm_IRFC.json").read_text())
    parsed = [NSEDataParser.parse_quote_row(r, today=TODAY) for r in raw["data"]]
    return {q["series_code"]: q for q in parsed}


@pytest.mark.parametrize(
    "code, family",
    [("11", "A"), ("01", "A"), ("XO", "B"), ("XN", "B"), ("XD", "B"), ("X1", "B"), ("X9", "B"),
     ("R3", "SPECIAL"), ("(O1)", "SPECIAL"), ("(ON)", "SPECIAL"), ("(OD)", "SPECIAL"),
     ("X0", "SPECIAL"), ("XX", "SPECIAL"), ("1", "SPECIAL"), ("", "SPECIAL")],
)
def test_series_family(code, family):
    assert series_family(code) == family


def test_irfc_quote_has_two_distinct_november_rows_family_a_and_b(quote_rows):
    november = [q for q in quote_rows.values() if q["month"] == "Nov-2026"]
    assert sorted((q["series_code"], q["series_family"]) for q in november) == [("11", "A"), ("XN", "B")]
    assert len(quote_rows) == 24
    assert quote_rows["R3"]["series_family"] == "SPECIAL"


def test_series_b_row_keeps_depth_traded_qty_and_nse_settlement(quote_rows):
    xn = quote_rows["XN"]
    assert xn["settlement_date"] == datetime.date(2026, 11, 3)
    assert xn["settlement_estimated"] is False
    assert (xn["best_bid"], xn["best_bid_qty"]) == (0.35, 10850)
    assert (xn["best_ask"], xn["best_ask_qty"]) == (0.7, 2000)
    assert xn["ltp"] == 0.31
    assert xn["traded_quantity"] == 1436482
    assert xn["open_positions"] == 14820912
    assert xn["annualized_yield"] == 4.89  # NSE's own figure
    assert xn["is_active"] is True


def test_settlement_falls_back_to_the_calendar_without_revlegsettdt(quote_rows):
    # 12 (Dec-2026) and X1 (Jan-2027) have no revLegSettDt; the table covers them.
    assert quote_rows["12"]["settlement_date"] == datetime.date(2026, 12, 1)
    assert quote_rows["12"]["settlement_estimated"] is False
    assert quote_rows["X1"]["settlement_date"] == datetime.date(2027, 1, 5)
    # X5 (May-2027) is beyond the table: first Tuesday, marked estimated.
    assert quote_rows["X5"]["settlement_date"] == datetime.date(2027, 5, 4)
    assert quote_rows["X5"]["settlement_estimated"] is True


def test_zero_underlying_gives_no_yield_rather_than_a_wrong_one(quote_rows):
    xo = quote_rows["XO"]  # NSE sends underLtp = 0 and annulisedYieldPer = 0 here
    assert xo["underlying_price"] == 0.0
    assert xo["annualized_yield"] == 0.0


def test_compute_annualized_yield_is_decimal_and_guards_inputs():
    d = TODAY + datetime.timedelta(days=365)
    assert NSEDataParser.compute_annualized_yield(1.0, 100.0, d, today=TODAY) == 1.0
    # 0.35 / 77.06 × 365 / 33 × 100 = 5.0236... -> 5.02
    assert NSEDataParser.compute_annualized_yield(0.35, 77.06, datetime.date(2026, 11, 3), today=TODAY) == 5.02
    assert NSEDataParser.compute_annualized_yield(0.35, 0.0, d, today=TODAY) == 0.0
    assert NSEDataParser.compute_annualized_yield(0.0, 77.06, d, today=TODAY) == 0.0
    assert NSEDataParser.compute_annualized_yield(0.35, 77.06, TODAY, today=TODAY) == 0.0
    assert NSEDataParser.compute_annualized_yield(0.35, 77.06, None, today=TODAY) == 0.0


def test_quote_row_to_market_data_keeps_code_family_settlement_and_qty(quote_rows):
    md = NSEDataParser.quote_row_to_market_data(quote_rows["XN"], "IRFC")
    assert md.symbol == "IRFC"
    assert md.series == "Nov-2026"
    assert md.series_code == "XN" and md.series_family == "B"
    assert md.settlement_date == datetime.date(2026, 11, 3) and md.settlement_estimated is False
    assert md.expiry_date == datetime.date(2026, 11, 3)
    assert (md.best_bid, md.best_bid_qty, md.best_ask, md.best_ask_qty) == (0.35, 10850, 0.7, 2000)
    assert md.ltp == 0.31  # last traded fee, not the close
    assert md.volume == 1436482


def test_parse_nse_date_formats():
    assert NSEDataParser.parse_nse_date("06-Oct-2026") == datetime.date(2026, 10, 6)
    assert NSEDataParser.parse_nse_date("03-AUG-2027") == datetime.date(2027, 8, 3)
    assert NSEDataParser.parse_nse_date(None) is None
    assert NSEDataParser.parse_nse_date("2026-10-06") is None


def test_history_rows_parse():
    raw = json.loads((FIXTURES / "slb_history_IRFC.json").read_text())
    rows = [NSEDataParser.parse_history_row(r) for r in raw]
    assert len(rows) == 70 and all(rows)
    first = rows[0]
    assert first["trade_date"] == datetime.date(2026, 10, 1)
    assert first["series_code"] == "X8" and first["series_family"] == "B"
    assert first["settlement_date"] == datetime.date(2027, 8, 3)
    assert (first["close"], first["quantity"], first["trades"]) == (33.23, 1, 1)


def test_existing_market_data_callers_are_unaffected():
    md = NSEDataParser.parse_raw_slb_item({"symbol": "IRFC", "series": "Nov-2026"})
    assert md.series_code is None and md.series_family is None and md.settlement_date is None
