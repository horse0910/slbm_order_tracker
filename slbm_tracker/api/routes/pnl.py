"""P&L API endpoints (Phase 3 feature 7).

Exposes the authoritative backend P&L engine: a stateless breakdown preview, and
compute-and-persist per contract. All money math is done in the service (Decimal);
these handlers only marshal. The tax figure is a clearly-labeled estimate.
"""

from __future__ import annotations

import logging
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from slbm_tracker.api.schemas import (
    PnLBreakdownResponse,
    PnLListResponse,
    PnLPreviewRequest,
    PnLRecordResponse,
    PnLTotals,
)
from slbm_tracker.db.session import get_db
from slbm_tracker.services.pnl_service import PnLService
from slbm_tracker.utils.money import money_to_float, to_decimal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pnl", tags=["P&L"])


def _record_to_response(record, symbol: str, series) -> PnLRecordResponse:
    return PnLRecordResponse(
        id=record.id,
        contract_id=record.contract_id,
        symbol=symbol,
        series=series,
        gross_fee=record.gross_fee,
        processing_charge=record.processing_charge,
        gst=record.gst,
        dp_charge=record.dp_charge,
        net_income=record.net_income,
        tax_slab_pct=record.tax_slab_pct,
        estimated_tax=record.estimated_tax,
        post_tax_income=record.post_tax_income,
        created_at=record.created_at,
    )


@router.post("/preview", response_model=PnLBreakdownResponse)
def preview_pnl(payload: PnLPreviewRequest) -> PnLBreakdownResponse:
    """Compute an itemized P&L breakdown without persisting anything."""
    b = PnLService.compute_breakdown(
        payload.quantity_lent, payload.lending_fee, tax_slab_pct=payload.tax_slab_pct
    )
    return PnLBreakdownResponse(**b.__dict__)


@router.post("/contracts/{contract_id}", response_model=PnLRecordResponse)
def record_contract_pnl(contract_id: int, db: Session = Depends(get_db)) -> PnLRecordResponse:
    """Compute and persist (idempotent) the P&L record for a contract."""
    try:
        record = PnLService.record_for_contract(db=db, contract_id=contract_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )
    # Re-read joined for symbol/series.
    records = PnLService.list_records(db=db)
    for rec, symbol, series in records:
        if rec.id == record.id:
            return _record_to_response(rec, symbol, series)
    # Fallback (should not happen): minimal response without join.
    return _record_to_response(record, "", None)


@router.get("/contracts/{contract_id}", response_model=PnLRecordResponse)
def get_contract_pnl(contract_id: int, db: Session = Depends(get_db)) -> PnLRecordResponse:
    """Get the persisted P&L record for a contract."""
    records = PnLService.list_records(db=db)
    for rec, symbol, series in records:
        if rec.contract_id == contract_id:
            return _record_to_response(rec, symbol, series)
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"No P&L record for contract {contract_id}. Compute it first.",
    )


@router.get("", response_model=PnLListResponse)
def list_pnl(db: Session = Depends(get_db)) -> PnLListResponse:
    """List all persisted P&L records with Decimal-summed totals."""
    rows = PnLService.list_records(db=db)

    fields = (
        "gross_fee",
        "processing_charge",
        "gst",
        "dp_charge",
        "net_income",
        "estimated_tax",
        "post_tax_income",
    )
    sums = {f: to_decimal(0) for f in fields}
    responses = []
    for rec, symbol, series in rows:
        for f in fields:
            sums[f] += to_decimal(getattr(rec, f))
        responses.append(_record_to_response(rec, symbol, series))

    totals = PnLTotals(
        records=len(rows),
        **{f: money_to_float(sums[f]) for f in fields},
    )
    return PnLListResponse(totals=totals, records=responses)
