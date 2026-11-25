"""NSE SLB Market Data API endpoints."""

from __future__ import annotations

import datetime
import logging
from typing import Any, AsyncGenerator, Optional
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from sqlalchemy.orm import Session

from slbm_tracker.api.schemas import SLBLiveResponse, SLBQuoteItem
from slbm_tracker.db.session import get_db
from slbm_tracker.services.contract_service import today_ist
from slbm_tracker.services.nse_client import NSEError
from slbm_tracker.services.nse_service import NSEService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/slb", tags=["SLB Market Data"])


async def get_nse_service(
    request: Request,
    db: Session = Depends(get_db),
) -> AsyncGenerator[NSEService, None]:
    """Provide an NSEService and guarantee its httpx client is closed.

    NSEService constructs an NSEClient (an ``httpx.AsyncClient`` owner) eagerly,
    so yielding it as a dependency and closing in ``finally`` prevents a
    per-request connection/socket leak on every SLB endpoint. ``close()`` is
    idempotent and a no-op when the client was never opened (e.g. the CSV
    import route never touches the network).
    """
    scheduler = getattr(request.app.state, "scheduler", None)
    svc = NSEService(db=db, client=scheduler._nse_client) if scheduler else NSEService(db=db)
    try:
        yield svc
    finally:
        if scheduler is None:
            await svc.client.close()


@router.get("/quote/{symbol}", response_model=dict[str, Any])
async def get_slb_quote(
    symbol: str,
    nse_service: NSEService = Depends(get_nse_service),
) -> dict[str, Any]:
    """Every SLB series for one stock, with its code (XN), family (A, B, SPECIAL),
    NSE settlement date and best bid/offer with quantities (Phase 2.11).

    Never a 500 on an NSE failure: a transport error falls back to the symbol
    view, a block (403) to the cached rows; ``source`` says which.
    """
    clean_sym = symbol.strip().upper()
    if not clean_sym:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Symbol cannot be empty.")
    return await nse_service.get_slb_quote(clean_sym)


@router.get("/quote/{symbol}/history", response_model=dict[str, Any])
async def get_slb_quote_history(
    symbol: str,
    from_date: Optional[datetime.date] = Query(None, alias="from", description="Default: 30 days before 'to'"),
    to_date: Optional[datetime.date] = Query(None, alias="to", description="Default: today (IST)"),
    nse_service: NSEService = Depends(get_nse_service),
) -> dict[str, Any]:
    """Daily SLB trades (OHLC fee per series) from NSE; it keeps about two months."""
    to_date = to_date or today_ist()
    from_date = from_date or to_date - datetime.timedelta(days=30)
    if from_date > to_date:
        raise HTTPException(status_code=422, detail="'from' must be on or before 'to'.")
    try:
        rows = await nse_service.get_slb_history(symbol, from_date, to_date)
    except NSEError as err:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"NSE history unavailable: {err}")
    return {
        "symbol": symbol.strip().upper(),
        "from": from_date.isoformat(),
        "to": to_date.isoformat(),
        "rows": rows,
    }


@router.get("/live/{symbol}", response_model=SLBLiveResponse)
async def get_live_slb_by_symbol(
    symbol: str,
    nse_service: NSEService = Depends(get_nse_service),
) -> SLBLiveResponse:
    """
    Fetch live SLB market contracts, bids, asks, and annualized yields for a specific stock.
    Automatically caches snapshots in database and falls back to cached data if live fetch fails.
    """
    clean_sym = symbol.strip().upper()
    if not clean_sym:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Symbol cannot be empty.",
        )

    items = await nse_service.get_slb_data_for_symbol(clean_sym)
    quote_items = [
        SLBQuoteItem(
            symbol=item.symbol,
            series=item.series,
            expiry_date=item.expiry_date,
            best_bid=item.best_bid,
            best_bid_qty=item.best_bid_qty,
            best_ask=item.best_ask,
            best_ask_qty=item.best_ask_qty,
            ltp=item.ltp,
            underlying_price=item.underlying_price,
            spread=item.spread,
            spread_pct=item.spread_pct,
            open_positions=item.open_positions,
            volume=item.volume,
            turnover=item.turnover,
            annualized_yield=item.annualized_yield,
            corporate_action=item.corporate_action,
            is_slb_eligible=item.is_slb_eligible,
            timestamp=item.timestamp,
        )
        for item in items
    ]

    return SLBLiveResponse(
        symbol=clean_sym,
        contracts_count=len(quote_items),
        contracts=quote_items,
    )


@router.get("/series/{series}", response_model=list[SLBQuoteItem])
async def get_live_slb_by_series(
    series: str,
    nse_service: NSEService = Depends(get_nse_service),
) -> list[SLBQuoteItem]:
    """Fetch SLB market data for all securities under a specific contract series month (e.g. '10' or 'Mar-2027')."""
    items = await nse_service.get_slb_data_for_series(series)
    return [
        SLBQuoteItem(
            symbol=item.symbol,
            series=item.series,
            expiry_date=item.expiry_date,
            best_bid=item.best_bid,
            best_bid_qty=item.best_bid_qty,
            best_ask=item.best_ask,
            best_ask_qty=item.best_ask_qty,
            ltp=item.ltp,
            underlying_price=item.underlying_price,
            spread=item.spread,
            spread_pct=item.spread_pct,
            open_positions=item.open_positions,
            volume=item.volume,
            turnover=item.turnover,
            annualized_yield=item.annualized_yield,
            corporate_action=item.corporate_action,
            is_slb_eligible=item.is_slb_eligible,
            timestamp=item.timestamp,
        )
        for item in items
    ]


@router.get("/series-master", response_model=dict[str, Any])
async def get_series_master(
    nse_service: NSEService = Depends(get_nse_service),
) -> dict[str, Any]:
    """Fetch active SLB series months metadata directly from NSE."""
    return await nse_service.get_series_master()


@router.get("/eligible", response_model=list[str])
async def get_eligible_securities(
    nse_service: NSEService = Depends(get_nse_service),
) -> list[str]:
    """Fetch list of all SLB-eligible security symbols."""
    return await nse_service.get_eligible_securities()


@router.post("/import-report", response_model=dict[str, Any])
async def import_offline_slb_report(
    file: UploadFile = File(..., description="NSE daily SLB CSV report file"),
    nse_service: NSEService = Depends(get_nse_service),
) -> dict[str, Any]:
    """Ingest an offline EOD CSV report from the NSE All Reports portal."""
    content_bytes = await file.read()
    try:
        csv_text = content_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        csv_text = content_bytes.decode("latin-1")

    saved_snapshots = nse_service.ingest_csv_report(csv_text)
    return {
        "status": "success",
        "snapshots_saved": len(saved_snapshots),
        "message": f"Successfully ingested {len(saved_snapshots)} records from {file.filename}",
    }
