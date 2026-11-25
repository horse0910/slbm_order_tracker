"""Unit tests for NSE SLB JSON data parser and expiry calculations."""

import datetime
from slbm_tracker.services.nse_parser import NSEDataParser


def test_first_tuesday_calculation():
    # Oct 2026: Oct 1 is Thursday -> first Tuesday is Oct 6
    assert NSEDataParser.calculate_first_tuesday(2026, 10) == datetime.date(2026, 10, 6)

    # Nov 2026: Nov 1 is Sunday -> first Tuesday is Nov 3
    assert NSEDataParser.calculate_first_tuesday(2026, 11) == datetime.date(2026, 11, 3)

    # Dec 2026: Dec 1 is Tuesday -> first Tuesday is Dec 1
    assert NSEDataParser.calculate_first_tuesday(2026, 12) == datetime.date(2026, 12, 1)

    # Jan 2027: Jan 1 is Friday -> first Tuesday is Jan 5
    assert NSEDataParser.calculate_first_tuesday(2027, 1) == datetime.date(2027, 1, 5)


def test_parse_series_expiry():
    assert NSEDataParser.parse_series_expiry("Oct-2026") == datetime.date(2026, 10, 6)
    assert NSEDataParser.parse_series_expiry("Mar-2027") == datetime.date(2027, 3, 2)
    assert NSEDataParser.parse_series_expiry("Oct-2026(OD)**") == datetime.date(2026, 10, 6)
    assert NSEDataParser.parse_series_expiry("") is None
    assert NSEDataParser.parse_series_expiry("INVALID") is None


def test_parse_api_response(sample_nse_json: dict):
    results = NSEDataParser.parse_api_response(sample_nse_json)
    assert len(results) == 2

    row1 = results[0]
    assert row1.symbol == "RELIANCE"
    assert row1.series == "Oct-2026"
    assert row1.expiry_date == datetime.date(2026, 10, 6)
    assert row1.best_bid == 4.50
    assert row1.best_bid_qty == 500
    assert row1.best_ask == 5.25
    assert row1.best_ask_qty == 1000
    assert row1.ltp == 5.00
    assert row1.underlying_price == 2850.50
    assert row1.annualized_yield == 8.55
    assert row1.volume == 3500
    assert row1.open_positions == 15200
    assert row1.corporate_action == "DIVIDEND - RS 6 PER SHARE"
    assert row1.is_slb_eligible is True

    row2 = results[1]
    assert row2.series == "Nov-2026"
    assert row2.expiry_date == datetime.date(2026, 11, 3)
    assert row2.best_bid == 8.00
    assert row2.annualized_yield == 7.80


def test_parser_numeric_defaults():
    raw_item = {
        "symbol": "TCS",
        "series": "Oct-2026",
        "buyOrderPrice1": None,
        "sellOrderPrice1": "-",
        "lastTradedPrice": "1,250.75",
        "openPositions": "12,000",
    }
    parsed = NSEDataParser.parse_raw_slb_item(raw_item)
    assert parsed is not None
    assert parsed.best_bid == 0.0
    assert parsed.best_ask == 0.0
    assert parsed.ltp == 1250.75
    assert parsed.open_positions == 12000


def test_parsed_records_get_distinct_current_timestamps():
    """Regression (M-1): the timestamp must be per-instance and current, not a
    single value frozen at import time via a bare default."""
    before = datetime.datetime.utcnow()
    first = NSEDataParser.parse_raw_slb_item({"symbol": "AAA", "series": "Oct-2026"})
    second = NSEDataParser.parse_raw_slb_item({"symbol": "BBB", "series": "Oct-2026"})
    after = datetime.datetime.utcnow()

    assert first is not None and second is not None
    # Each record carries its own timestamp (not a shared frozen default).
    assert first.timestamp is not second.timestamp
    assert second.timestamp >= first.timestamp
    # Timestamps reflect parse time (now), not process import time.
    assert before <= first.timestamp <= after
    assert before <= second.timestamp <= after
