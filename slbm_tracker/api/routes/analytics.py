"""Analytics API endpoint (Phase 3 feature 6, read-only).

Surfaces :class:`AnalyticsService` roll-ups. All aggregation and money math is
backend-owned; this handler only marshals. No forecasting or invented data.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from slbm_tracker.api.schemas import (
    AnalyticsSummaryResponse,
    EquityBarResponse,
    EquityHistoryResponse,
    EquityRefreshResponse,
    EquityStatsResponse,
    FinancialYearIncomeResponse,
    SLBHistoryPointResponse,
    SLBHistoryResponse,
    SymbolYieldResponse,
)
from slbm_tracker.config import get_settings
from slbm_tracker.db.session import get_db
from slbm_tracker.services.analytics_service import AnalyticsService
from slbm_tracker.services.equity_history_service import EquityHistoryService
from slbm_tracker.services.nse_client import NSEError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analytics", tags=["Analytics"])


@router.get("/summary", response_model=AnalyticsSummaryResponse)
def analytics_summary(db: Session = Depends(get_db)) -> AnalyticsSummaryResponse:
    """Income-by-FY, contract status breakdown, and per-symbol yield stats."""
    summary = AnalyticsService.build_summary(db=db)
    return AnalyticsSummaryResponse(
        generated_at=summary.generated_at,
        status_breakdown=summary.status_breakdown,
        income_by_financial_year=[
            FinancialYearIncomeResponse(
                financial_year=fy.financial_year,
                contracts=fy.contracts,
                gross_fee=fy.gross_fee,
                net_income=fy.net_income,
                estimated_tax=fy.estimated_tax,
                post_tax_income=fy.post_tax_income,
            )
            for fy in summary.income_by_financial_year
        ],
        yield_by_symbol=[
            SymbolYieldResponse(
                symbol=s.symbol,
                latest_yield=s.latest_yield,
                average_yield=s.average_yield,
                samples=s.samples,
            )
            for s in summary.yield_by_symbol
        ],
    )


@router.get("/history", response_model=SLBHistoryResponse)
def slb_history(
    symbol: Optional[str] = Query(None, description="Symbol to trend; omit for owned-portfolio aggregate."),
    days: int = Query(30, ge=1, le=365, description="Trailing window in days."),
    db: Session = Depends(get_db),
) -> SLBHistoryResponse:
    """Daily SLB yield/spread/open-positions trend from persisted snapshots.

    Per-symbol when ``symbol`` is given, else aggregated across owned holdings.
    ``days`` is clamped to the snapshot-retention horizon (older data has been
    pruned, so a larger window would be misleading)."""
    settings = get_settings()
    if settings.snapshot_retention_days > 0:
        days = min(days, settings.snapshot_retention_days)

    if symbol:
        points = AnalyticsService.yield_history(db, symbol, days)
        scope, sym = "symbol", symbol.strip().upper()
    else:
        points = AnalyticsService.portfolio_yield_history(db, days)
        scope, sym = "portfolio", None

    return SLBHistoryResponse(
        scope=scope,
        symbol=sym,
        days=days,
        points=[
            SLBHistoryPointResponse(
                date=p.date,
                annualized_yield=p.annualized_yield,
                spread=p.spread,
                open_positions=p.open_positions,
                samples=p.samples,
            )
            for p in points
        ],
    )


@router.get("/equity-history", response_model=EquityHistoryResponse)
def equity_history(
    symbol: str = Query(..., min_length=1, description="Owned symbol to read stored history for."),
    days: Optional[int] = Query(None, ge=1, le=1825, description="Window in days (default from settings)."),
    db: Session = Depends(get_db),
) -> EquityHistoryResponse:
    """Read persisted daily price/volume bars + descriptive stats for a symbol.

    Read-only: does NOT hit NSE (use POST /equity-history/refresh to fetch).
    Returns empty bars and zero-sample stats when nothing is stored yet."""
    window = days or get_settings().equity_history_days
    bars = AnalyticsService.equity_history(db, symbol, window)
    stats = AnalyticsService.equity_stats(db, symbol, window)
    return EquityHistoryResponse(
        symbol=symbol.strip().upper(),
        days=window,
        bars=[EquityBarResponse.model_validate(b) for b in bars],
        stats=EquityStatsResponse(
            symbol=stats.symbol,
            samples=stats.samples,
            period_start=stats.period_start,
            period_end=stats.period_end,
            first_close=stats.first_close,
            last_close=stats.last_close,
            period_high=stats.period_high,
            period_low=stats.period_low,
            period_return_pct=stats.period_return_pct,
            avg_volume=stats.avg_volume,
            daily_return_volatility_pct=stats.daily_return_volatility_pct,
        ),
    )


@router.post("/equity-history/refresh", response_model=EquityRefreshResponse)
async def refresh_equity_history(
    symbol: Optional[str] = Query(None, description="Symbol to refresh; omit (or 'all') to refresh all owned holdings."),
    days: Optional[int] = Query(None, ge=1, le=1825, description="Window in days (default from settings)."),
    db: Session = Depends(get_db),
) -> EquityRefreshResponse:
    """Fetch equity price/volume history from NSE and UPSERT it locally.

    This is the only analytics route that reaches NSE. A NSE failure surfaces as
    502 (honest failure) rather than a silent empty refresh."""
    service = EquityHistoryService(db)
    try:
        if symbol and symbol.strip().lower() != "all":
            written = await service.refresh_symbol(symbol, days)
            refreshed = {symbol.strip().upper(): written}
        else:
            refreshed = await service.refresh_owned(days)
    except NSEError as exc:
        logger.warning("Equity-history refresh failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"NSE fetch failed: {exc}") from exc
    finally:
        await service.aclose()

    return EquityRefreshResponse(
        refreshed=refreshed,
        symbols=len(refreshed),
        total_bars=sum(refreshed.values()),
    )


@router.get("/slb-trades")
def archived_slb_trades(request: Request, symbol: Optional[str] = None, days: int = Query(365, ge=1, le=365), offset: int = Query(0,ge=0), limit: int = Query(100,ge=1,le=500), expiry: Optional[str] = None, db: Session = Depends(get_db)):
    return request.app.state.slb_history.read(db, symbol, days, offset, limit, expiry)


@router.post("/slb-trades/refresh")
async def refresh_archived_slb_trades(request: Request):
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        raise HTTPException(status_code=409, detail="Start the market monitor before requesting historical imports.")
    return await request.app.state.slb_history.start(scheduler._nse_client, scheduler)


@router.get("/slb-trades.csv")
def export_archived_slb_trades(request: Request, symbol: Optional[str] = None, days: int = Query(365,ge=1,le=365), expiry: Optional[str] = None, db: Session = Depends(get_db)):
    import csv,io
    from fastapi.responses import Response
    data=request.app.state.slb_history.read(db,symbol,days,limit=0,expiry=expiry)
    output=io.StringIO()
    fields=['symbol','company_name','series_code','trade_date','settlement_date','open','high','low','close','quantity','turnover','vwap','trades','source']
    writer=csv.DictWriter(output,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(data['rows'])
    return Response(output.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="slb-daily-history.csv"'})
