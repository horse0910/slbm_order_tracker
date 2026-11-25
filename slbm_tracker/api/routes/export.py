"""CSV export API (Phase 5C).

A thin, read-only serialization layer over the existing Phase-3 / 5B services:
persisted P&L records, derived contract views, analytics roll-ups, and the SLB
yield trend. There is NO recompute and NO money math here — every figure comes
straight from the owning service; this router only shapes it into CSV rows (via
:mod:`slbm_tracker.utils.csv_export`) and attaches download headers.

Fully local and dependency-free: stdlib ``csv`` over an in-memory ``StringIO``.
PDF is produced by the browser's print dialog on the frontend (a ``@media print``
stylesheet + "Print / Save as PDF"), never server-side.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from slbm_tracker.config import get_settings
from slbm_tracker.db.session import get_db
from slbm_tracker.services.analytics_service import AnalyticsService
from slbm_tracker.services.contract_service import ContractService
from slbm_tracker.services.pnl_service import PnLService
from slbm_tracker.utils.csv_export import fmt_cell, fmt_money, fmt_num, rows_to_csv, to_csv

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/export", tags=["Export"])

_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_part(value: str) -> str:
    """Sanitize a user-supplied string for use inside a download filename.

    Keeps only ``[A-Za-z0-9._-]`` so a crafted ``symbol`` query value can never
    inject a quote or CR/LF into the ``Content-Disposition`` header (HTTP
    response-splitting defense — cheap even though this app is localhost-only).
    """
    cleaned = _UNSAFE_FILENAME.sub("", value.strip())
    return cleaned or "export"


def _csv_response(filename: str, body: str) -> Response:
    """Wrap a CSV string as an attachment download.

    ``text/csv`` + ``Content-Disposition: attachment`` so the browser saves the
    file rather than rendering it; ``charset=utf-8`` keeps any non-ASCII symbol
    names intact.
    """
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/pnl.csv")
def export_pnl_csv(db: Session = Depends(get_db)) -> Response:
    """Persisted P&L records, one row each (money as fixed-2 strings).

    Serializes exactly what :meth:`PnLService.list_records` returns — no recompute
    — joined to the contract's symbol/series.
    """
    rows = PnLService.list_records(db=db)
    header = [
        "contract_id",
        "symbol",
        "series",
        "gross_fee",
        "processing_charge",
        "gst",
        "dp_charge",
        "net_income",
        "tax_slab_pct",
        "estimated_tax",
        "post_tax_income",
        "created_at",
    ]
    data = [
        [
            fmt_num(rec.contract_id),
            fmt_cell(symbol),
            fmt_cell(series),
            fmt_money(rec.gross_fee),
            fmt_money(rec.processing_charge),
            fmt_money(rec.gst),
            fmt_money(rec.dp_charge),
            fmt_money(rec.net_income),
            fmt_num(rec.tax_slab_pct),
            fmt_money(rec.estimated_tax),
            fmt_money(rec.post_tax_income),
            fmt_cell(rec.created_at),
        ]
        for rec, symbol, series in rows
    ]
    return _csv_response("pnl.csv", to_csv(header, data))


@router.get("/contracts.csv")
def export_contracts_csv(db: Session = Depends(get_db)) -> Response:
    """Derived contract views: dates, lifecycle status, days-remaining, income.

    Reads :meth:`ContractService.list_contracts`, so ``effective_status`` and
    ``days_remaining`` are the backend-derived, date-consistent values — the CSV
    never recomputes lifecycle from dates. ``lending_fee`` is a per-share RATE
    (``fmt_num``); ``lending_fee_income`` is a money total (``fmt_money``).
    """
    views = ContractService.list_contracts(db=db)
    header = [
        "id",
        "symbol",
        "series",
        "trade_date",
        "expiry_date",
        "reverse_leg_date",
        "quantity_lent",
        "lending_fee",
        "stored_status",
        "effective_status",
        "days_remaining",
        "tenure_days",
        "lending_fee_income",
        "is_rollover_candidate",
    ]
    data = [
        [
            fmt_num(v.id),
            fmt_cell(v.symbol),
            fmt_cell(v.series),
            fmt_cell(v.trade_date),
            fmt_cell(v.expiry_date),
            fmt_cell(v.reverse_leg_date),
            fmt_num(v.quantity_lent),
            fmt_num(v.lending_fee),
            fmt_cell(v.stored_status),
            fmt_cell(v.effective_status),
            fmt_num(v.days_remaining),
            fmt_num(v.tenure_days),
            fmt_money(v.lending_fee_income),
            fmt_cell(v.is_rollover_candidate),
        ]
        for v in views
    ]
    return _csv_response("contracts.csv", to_csv(header, data))


@router.get("/analytics.csv")
def export_analytics_csv(db: Session = Depends(get_db)) -> Response:
    """Analytics roll-up as a two-section report: FY income + per-symbol yield.

    Both sections come from :meth:`AnalyticsService.build_summary` (no recompute).
    They have different shapes, so the CSV stacks two titled tables separated by a
    blank line — a common, spreadsheet-friendly layout. Money totals use fixed-2
    strings; yields are plain numbers.
    """
    summary = AnalyticsService.build_summary(db=db)

    rows: list[list[str]] = []
    # Section 1 — income by financial year.
    rows.append(["Income by financial year"])
    rows.append(
        ["financial_year", "contracts", "gross_fee", "net_income", "estimated_tax", "post_tax_income"]
    )
    for fy in summary.income_by_financial_year:
        rows.append(
            [
                fmt_cell(fy.financial_year),
                fmt_num(fy.contracts),
                fmt_money(fy.gross_fee),
                fmt_money(fy.net_income),
                fmt_money(fy.estimated_tax),
                fmt_money(fy.post_tax_income),
            ]
        )

    rows.append([])  # blank separator row

    # Section 2 — yield by symbol.
    rows.append(["Yield by symbol"])
    rows.append(["symbol", "latest_yield", "average_yield", "samples"])
    for s in summary.yield_by_symbol:
        rows.append(
            [
                fmt_cell(s.symbol),
                fmt_num(s.latest_yield),
                fmt_num(s.average_yield),
                fmt_num(s.samples),
            ]
        )

    return _csv_response("analytics.csv", rows_to_csv(rows))


@router.get("/history.csv")
def export_history_csv(
    symbol: Optional[str] = Query(
        None, description="Symbol to trend; omit for the owned-portfolio aggregate."
    ),
    days: int = Query(30, ge=1, le=365, description="Trailing window in days."),
    db: Session = Depends(get_db),
) -> Response:
    """Daily SLB yield/spread/open-positions trend (reuses the 5B analytics service).

    Per-symbol when ``symbol`` is given, else aggregated across owned holdings.
    ``days`` is clamped to the snapshot-retention horizon exactly as the
    ``GET /api/analytics/history`` route does, so the export can never claim a
    wider window than the data actually spans.
    """
    settings = get_settings()
    if settings.snapshot_retention_days > 0:
        days = min(days, settings.snapshot_retention_days)

    if symbol:
        points = AnalyticsService.yield_history(db, symbol, days)
        scope = symbol.strip().upper()
    else:
        points = AnalyticsService.portfolio_yield_history(db, days)
        scope = "portfolio"

    header = ["date", "annualized_yield", "spread", "open_positions", "samples"]
    data = [
        [
            fmt_cell(p.date),
            fmt_num(p.annualized_yield),
            fmt_num(p.spread),
            fmt_num(p.open_positions),
            fmt_num(p.samples),
        ]
        for p in points
    ]
    filename = f"slb-history-{_safe_part(scope).lower()}-{days}d.csv"
    return _csv_response(filename, to_csv(header, data))
