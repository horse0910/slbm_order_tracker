"""Unit tests for NSE CSV Reports parser."""

import datetime
from slbm_tracker.services.nse_reports import NSEReportsService


def test_parse_slb_csv_report():
    sample_report = (
        "Symbol,Series,Expiry Date,Last Traded Price,Best Bid,Best Ask,Volume,Open Positions,Annualised Yield\n"
        "RELIANCE,Oct-2026,06-Oct-2026,5.00,4.50,5.25,3500,15200,8.55\n"
        "TCS,Oct-2026,06-Oct-2026,8.50,8.00,9.00,1200,5400,7.80\n"
    )

    results = NSEReportsService.parse_slb_csv_report(sample_report)
    assert len(results) == 2

    r1 = results[0]
    assert r1.symbol == "RELIANCE"
    assert r1.series == "Oct-2026"
    assert r1.expiry_date == datetime.date(2026, 10, 6)
    assert r1.ltp == 5.00
    assert r1.best_bid == 4.50
    assert r1.best_ask == 5.25
    assert r1.volume == 3500
    assert r1.open_positions == 15200
    assert r1.annualized_yield == 8.55
    assert r1.spread == 0.75

    r2 = results[1]
    assert r2.symbol == "TCS"
    assert r2.ltp == 8.50


def test_parse_empty_or_malformed_csv_report():
    assert len(NSEReportsService.parse_slb_csv_report("")) == 0
    assert len(NSEReportsService.parse_slb_csv_report("Foo,Bar\n1,2")) == 0
