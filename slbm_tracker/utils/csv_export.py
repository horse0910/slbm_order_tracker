"""Shared CSV serialization helpers for the export API (Phase 5C).

Pure and dependency-free: stdlib :mod:`csv` over an in-memory ``StringIO``. NO
recompute and NO money math live here — callers pass already-computed service
values and this module only *renders* them consistently (fixed-2 money strings,
ISO dates, blank cells for ``None``) and lets the ``csv`` writer handle quoting.
Kept separate from the route so the formatting/quoting is unit-testable without
spinning up the app.

Money vs. rate distinction (mirrors :mod:`slbm_tracker.utils.money`):
* :func:`fmt_money` renders currency TOTALS at a fixed 2 dp. Values arriving here
  are already correctly rounded to paise by the service layer; this only renders
  them — it never re-rounds or does arithmetic.
* :func:`fmt_num` renders plain numbers (per-share RATES, percentages, counts)
  as-is, so their input precision is preserved (never quantized like money).
"""

from __future__ import annotations

import csv
import io
from typing import Iterable, Sequence

# Leading characters a spreadsheet may interpret as the start of a formula.
_FORMULA_PREFIXES = ("=", "+", "-", "@")


def fmt_money(value) -> str:
    """Render a money total as a fixed 2-dp string; ``None`` → blank cell."""
    if value is None:
        return ""
    return f"{float(value):.2f}"


def fmt_num(value) -> str:
    """Render a plain number (rate/percent/count) as-is; ``None`` → blank cell.

    Not a money total, so no fixed-2 quantization — per-share rates and
    percentages keep their given precision.
    """
    if value is None:
        return ""
    if isinstance(value, bool):  # guard: bool is an int subclass
        return "true" if value else "false"
    return str(value)


def fmt_cell(value) -> str:
    """Render a generic (text/date/bool) cell; ``None`` → blank cell.

    ``date``/``datetime`` render via ``isoformat`` for a stable, sortable cell
    (naive timestamps stay naive — no trailing "Z", matching the app convention).
    Free-text cells get a CSV formula-injection guard: a value a spreadsheet
    could execute as a formula is neutralized with a leading apostrophe. This app
    is single-user and local, so that guard is defense-in-depth, not a trust
    boundary; numeric cells go through :func:`fmt_money`/:func:`fmt_num` and are
    never touched by it.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        return iso()
    text = str(value)
    if text and text[0] in _FORMULA_PREFIXES:
        return "'" + text
    return text


def rows_to_csv(rows: Iterable[Sequence]) -> str:
    """Render arbitrary rows to a CSV string with stdlib QUOTE_MINIMAL quoting.

    Cells are written untouched so any value containing a comma/quote/newline is
    quoted correctly by the writer. Callers are expected to have already string-
    formatted their cells via the ``fmt_*`` helpers.
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    for row in rows:
        writer.writerow(list(row))
    return buf.getvalue()


def to_csv(header: Sequence[str], rows: Iterable[Sequence]) -> str:
    """Render a single-table CSV: one header row followed by ``rows``.

    An empty ``rows`` yields a header-only CSV (never a crash) — the contract the
    export endpoints rely on for empty datasets.
    """
    return rows_to_csv([list(header), *[list(r) for r in rows]])
