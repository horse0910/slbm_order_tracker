"""SLB reverse-leg (settlement) dates when NSE gives none (Phase 2.10).

NSE's quote API carries the settlement date per series (``revLegSettDt``) and
that always wins. This module is only the fallback for a series without it.

The reverse leg settles on the FIRST TUESDAY of the series month (since the
October 2025 contracts), moved to the next working day if that Tuesday is an
exchange holiday. ``OVERRIDES`` and ``HOLIDAYS`` are copied as-is from T's
``calendar_slb.py``: the dates NSE Clearing actually published. Beyond that
table the first-Tuesday rule is applied and the result is marked *estimated*.
Do not add dates here from memory; extend the table only from NSE Clearing's
settlement-calendar circulars.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Optional

# Exchange trading holidays (copied from T's calendar_slb.py). Keep current from
# https://www.nseindia.com/resources/exchange-communication-holidays
HOLIDAYS: set[dt.date] = {
    dt.date(2026, 3, 3),   # Holi -- pushed the Mar 2026 reverse leg to Wed 4 Mar
}

# Reverse leg dates as actually published in NSE Clearing settlement-calendar
# circulars (copied from T's calendar_slb.py). These always win over the
# computed date.
OVERRIDES: dict[tuple[int, int], dt.date] = {
    (2026, 2): dt.date(2026, 2, 3),
    (2026, 3): dt.date(2026, 3, 4),
    (2026, 4): dt.date(2026, 4, 7),
    (2026, 5): dt.date(2026, 5, 5),
    (2026, 6): dt.date(2026, 6, 2),
    (2026, 7): dt.date(2026, 7, 7),
    (2026, 8): dt.date(2026, 8, 4),
    (2026, 9): dt.date(2026, 9, 1),
    (2026, 10): dt.date(2026, 10, 6),
    (2026, 11): dt.date(2026, 11, 3),
    (2026, 12): dt.date(2026, 12, 1),
    (2027, 1): dt.date(2027, 1, 5),
    (2027, 2): dt.date(2027, 2, 2),
}

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
_LABEL = re.compile(r"([A-Za-z]{3})[-/\s]*(\d{4})")


def first_tuesday(year: int, month: int) -> dt.date:
    d = dt.date(year, month, 1)
    # Monday=0 ... Tuesday=1
    return d + dt.timedelta(days=(1 - d.weekday()) % 7)


def settlement_date_for_month(year: int, month: int) -> tuple[dt.date, bool]:
    """``(reverse-leg date, estimated)`` for a series month.

    From ``OVERRIDES`` when published (not estimated); otherwise the first
    Tuesday rolled past weekends and ``HOLIDAYS``, marked estimated.
    """
    if (year, month) in OVERRIDES:
        return OVERRIDES[(year, month)], False
    d = first_tuesday(year, month)
    while d.weekday() >= 5 or d in HOLIDAYS:
        d += dt.timedelta(days=1)
    return d, True


def settlement_date_for_label(label: str) -> Optional[tuple[dt.date, bool]]:
    """Same, from a month label such as ``'Oct-2026'`` or ``'Oct-2026(O1)**'``."""
    match = _LABEL.search(re.sub(r"\(.*?\)", "", label or ""))
    if not match or match.group(1).upper() not in _MONTHS:
        return None
    return settlement_date_for_month(int(match.group(2)), _MONTHS[match.group(1).upper()])
