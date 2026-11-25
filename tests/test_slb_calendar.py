"""Phase 2.10: settlement-date fallback when NSE gives no revLegSettDt.

OVERRIDES and HOLIDAYS are copied as-is from T's calendar_slb.py (dates from
NSE Clearing circulars). Beyond the table the first-Tuesday rule applies and
the date is marked estimated. No dates are added from memory.
"""

from __future__ import annotations

import datetime

from slbm_tracker.utils.slb_calendar import HOLIDAYS, OVERRIDES, settlement_date_for_month


def test_march_2026_is_the_holi_shift_not_the_first_tuesday():
    d, estimated = settlement_date_for_month(2026, 3)
    assert d == datetime.date(2026, 3, 4)  # first Tuesday 3 Mar was Holi
    assert estimated is False


def test_months_in_the_table_come_from_the_circular():
    assert settlement_date_for_month(2026, 10) == (datetime.date(2026, 10, 6), False)
    assert settlement_date_for_month(2027, 2) == (datetime.date(2027, 2, 2), False)


def test_beyond_the_table_is_the_first_tuesday_marked_estimated():
    assert (2027, 3) not in OVERRIDES
    assert settlement_date_for_month(2027, 3) == (datetime.date(2027, 3, 2), True)
    assert settlement_date_for_month(2027, 9) == (datetime.date(2027, 9, 7), True)


def test_table_is_the_copy_from_t_and_nothing_more():
    assert min(OVERRIDES) == (2026, 2) and max(OVERRIDES) == (2027, 2)
    assert len(OVERRIDES) == 13
    assert HOLIDAYS == {datetime.date(2026, 3, 3)}


def test_month_label_parsing():
    from slbm_tracker.utils.slb_calendar import settlement_date_for_label

    assert settlement_date_for_label("Mar-2026") == (datetime.date(2026, 3, 4), False)
    assert settlement_date_for_label("Sep-2027") == (datetime.date(2027, 9, 7), True)
    assert settlement_date_for_label("Oct-2026(O1)**") == (datetime.date(2026, 10, 6), False)
    assert settlement_date_for_label("garbage") is None
