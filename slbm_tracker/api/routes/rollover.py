"""Rollover Advisor API endpoints (decision-support, read-only).

Surfaces :class:`RolloverService` output. This endpoint never places or
simulates an order and never fabricates future-series data — it reports only
what has been persisted from earlier exchange snapshots, flagging
``INSUFFICIENT_DATA`` when nothing is available.
"""

from __future__ import annotations

import logging
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from slbm_tracker.api.schemas import (
    RolloverOptionResponse,
    RolloverRecommendationResponse,
)
from slbm_tracker.db.session import get_db
from slbm_tracker.services.rollover_service import RolloverService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rollover", tags=["Rollover"])


def _option_to_response(opt) -> RolloverOptionResponse:
    return RolloverOptionResponse(
        series=opt.series,
        expiry_date=opt.expiry_date,
        market_fee=opt.market_fee,
        annualized_yield=opt.annualized_yield,
        open_positions=opt.open_positions,
        data_as_of=opt.data_as_of,
        series_code=opt.series_code,
        best_bid=opt.best_bid,
        best_bid_qty=opt.best_bid_qty,
        fillable=opt.fillable,
        net_fee=float(opt.net_fee) if opt.net_fee is not None else None,
        net_yield_pa=float(opt.net_yield_pa) if opt.net_yield_pa is not None else None,
        chain_days=opt.chain_days,
        within_cap=opt.within_cap,
    )


def _to_response(rec) -> RolloverRecommendationResponse:
    return RolloverRecommendationResponse(
        contract_id=rec.contract_id,
        stock_id=rec.stock_id,
        symbol=rec.symbol,
        current_series=rec.current_series,
        current_expiry=rec.current_expiry,
        current_fee=rec.current_fee,
        days_remaining=rec.days_remaining,
        status=rec.status,
        rationale=rec.rationale,
        best_option=_option_to_response(rec.best_option) if rec.best_option else None,
        fee_delta=rec.fee_delta,
        favorable=rec.favorable,
        options=[_option_to_response(o) for o in rec.options],
        origin_date=rec.origin_date,
        days_used=rec.days_used,
        days_used_label=rec.days_used_label,
    )


@router.get("", response_model=list[RolloverRecommendationResponse])
def list_rollover_recommendations(
    db: Session = Depends(get_db),
) -> list[RolloverRecommendationResponse]:
    """Rollover decision-support for contracts nearing expiry (most urgent first)."""
    recs = RolloverService.advise(db=db)
    return [_to_response(r) for r in recs]
