"""Portfolio management API endpoints."""

from __future__ import annotations

import datetime
import logging
from typing import Any, Optional
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from sqlalchemy.orm import Session

from slbm_tracker.api.schemas import (
    HoldingResponse,
    LentContractItem,
    PortfolioImportResponse,
    StockCreate,
    StockResponse,
    StockUpdate,
)
from slbm_tracker.db.models import Stock
from slbm_tracker.db.session import get_db
from slbm_tracker.services import live_board_service
from slbm_tracker.services.portfolio_service import PortfolioService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/portfolio", tags=["Portfolio"])


def _holdings(db: Session, stocks: list[Stock]) -> list[HoldingResponse]:
    """Attach in demat / lent / total owned and Lent badges to each stock."""
    positions = PortfolioService.holding_positions(db, stocks)
    out = []
    for stock in stocks:
        pos = positions[stock.id]
        out.append(
            HoldingResponse(
                **StockResponse.model_validate(stock).model_dump(),
                in_demat=pos.in_demat,
                lent=pos.lent,
                total_owned=pos.total_owned,
                lendable=pos.lendable,
                lent_contracts=[
                    LentContractItem(
                        contract_id=c.id,
                        series=c.series,
                        quantity_lent=c.quantity_lent,
                        trade_date=c.trade_date,
                        settlement_date=c.reverse_leg_date or c.expiry_date,
                    )
                    for c in pos.active_contracts
                ],
            )
        )
    return out


@router.get("", response_model=list[HoldingResponse])
def list_portfolio(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    is_slb_eligible: Optional[bool] = Query(None, description="Filter by SLB eligibility"),
    db: Session = Depends(get_db),
) -> list[HoldingResponse]:
    """List all stocks with in demat / lent out / total owned (lent shares included)."""
    stocks = PortfolioService.list_stocks(
        db=db, skip=skip, limit=limit, is_slb_eligible=is_slb_eligible
    )
    return _holdings(db, stocks)


@router.post("", response_model=StockResponse, status_code=status.HTTP_201_CREATED)
def add_stock(
    payload: StockCreate,
    db: Session = Depends(get_db),
) -> StockResponse:
    """Add a stock to the portfolio or update quantity if it already exists."""
    try:
        stock = PortfolioService.add_or_update_stock(
            db=db,
            symbol=payload.symbol,
            quantity=payload.quantity,
            avg_buy_price=payload.avg_buy_price,
            name=payload.name,
            isin=payload.isin,
            is_slb_eligible=payload.is_slb_eligible,
            notes=payload.notes,
        )
        return stock
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )


@router.get("/live", response_model=dict[str, Any])
def get_live_board(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Live board (Phase 2.13): every holding with its live SLB series, Series B
    first, each with its age; holdings not on the SLB board last. Reads stored
    snapshots only, never NSE. ``sweep_warning`` says when a full sweep cannot
    finish within live_refresh_seconds."""
    scheduler = getattr(request.app.state, "scheduler", None)
    return live_board_service.build_board(db, getattr(scheduler, "sweep_status", None))


@router.get("/live/trades", response_model=dict[str, Any])
def get_live_trades(
    since: Optional[datetime.datetime] = Query(
        None, description="ISO time; default: start of today (IST). Naive values are UTC."
    ),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Inferred trade events (traded-quantity rises between sweeps), newest first."""
    if since is not None and since.tzinfo is not None:
        since = since.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return live_board_service.trade_events(db, since)


@router.get("/{stock_id}", response_model=HoldingResponse)
def get_stock(
    stock_id: int,
    db: Session = Depends(get_db),
) -> HoldingResponse:
    """Get details of a specific stock in the portfolio."""
    stock = PortfolioService.get_stock(db=db, stock_id=stock_id)
    if not stock:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stock with ID {stock_id} not found.",
        )
    return _holdings(db, [stock])[0]


@router.put("/{stock_id}", response_model=StockResponse)
def update_stock(
    stock_id: int,
    payload: StockUpdate,
    db: Session = Depends(get_db),
) -> StockResponse:
    """Update holdings quantity or settings for an existing stock."""
    stock = PortfolioService.get_stock(db=db, stock_id=stock_id)
    if not stock:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stock with ID {stock_id} not found.",
        )

    if payload.name is not None:
        stock.name = payload.name
    if payload.isin is not None:
        stock.isin = payload.isin
    if payload.quantity is not None:
        stock.quantity = payload.quantity
    if payload.avg_buy_price is not None:
        stock.avg_buy_price = payload.avg_buy_price
    if payload.is_slb_eligible is not None:
        stock.is_slb_eligible = payload.is_slb_eligible
    if payload.notes is not None:
        stock.notes = payload.notes

    db.commit()
    db.refresh(stock)
    return stock


@router.delete("/{stock_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_stock(
    stock_id: int,
    db: Session = Depends(get_db),
) -> None:
    """Remove a stock from the portfolio."""
    deleted = PortfolioService.delete_stock(db=db, stock_id=stock_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stock with ID {stock_id} not found.",
        )


@router.post("/import", response_model=PortfolioImportResponse)
async def import_holdings_csv(
    file: UploadFile = File(..., description="CSV file exported from Zerodha Console or Kite"),
    db: Session = Depends(get_db),
) -> PortfolioImportResponse:
    """Bulk-import stock holdings from a Zerodha Console/Kite CSV file."""
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file must be a .csv file.",
        )

    content_bytes = await file.read()
    try:
        csv_text = content_bytes.decode("utf-8-sig")  # Handle possible UTF-8 BOM from Excel/Windows
    except UnicodeDecodeError:
        try:
            csv_text = content_bytes.decode("latin-1")
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unable to decode CSV file: {e}",
            )

    result = PortfolioService.import_zerodha_holdings(db=db, csv_content=csv_text)
    return PortfolioImportResponse(
        total_parsed=result["total_parsed"],
        added=result["added"],
        updated=result["updated"],
        errors=result["errors"],
        stocks=result["stocks"],
        zeroed=result["zeroed"],
        zeroing_skipped=result["zeroing_skipped"],
    )
