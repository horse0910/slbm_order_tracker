"""Active SLB lending-contract API endpoints.

Exposes contract creation and listing with derived, date-consistent lifecycle
state (see :mod:`slbm_tracker.services.contract_service`). The frontend
Contracts page distinguishes ACTIVE / EXPIRING_SOON / EXPIRED / rollover
candidates purely from the ``effective_status`` / ``is_rollover_candidate``
fields the backend computes — the UI never recomputes lifecycle from dates.
"""

from __future__ import annotations

import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from slbm_tracker.api.routes.rollover import _to_response as rollover_to_response
from slbm_tracker.api.schemas import (
    ContractCreate,
    ContractResponse,
    ContractRoll,
    ContractUpdate,
    RolloverRecommendationResponse,
)
from slbm_tracker.db.session import get_db
from slbm_tracker.db.models import LendingContract
from slbm_tracker.services.contract_service import ContractService, ContractStateError
from slbm_tracker.services.rollover_service import RolloverService
from slbm_tracker.services.lending_economics import evaluate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/contracts", tags=["Contracts"])


def _to_response(view, db=None) -> ContractResponse:
    history=[]
    if db is not None:
        records=db.query(LendingContract).filter(LendingContract.stock_id==view.stock_id).all()
        by_id={r.id:r for r in records};root=by_id.get(view.id);seen=set()
        while root and root.rolled_from_id and root.rolled_from_id not in seen:
            seen.add(root.id);root=by_id.get(root.rolled_from_id)
        ids={root.id} if root else {view.id}
        for _ in records:
            additions={r.id for r in records if r.rolled_from_id in ids}
            if additions<=ids:break
            ids|=additions
        history=[{"id":r.id,"series_code":r.series_code,"trade_date":r.trade_date,
                  "settlement_date":r.reverse_leg_date or r.expiry_date,"status":r.status,
                  "quantity":r.quantity_lent} for r in sorted(records,key=lambda r:r.id) if r.id in ids]

    economics = evaluate(view.quantity_lent, view.lending_fee, 0, view.tenure_days)
    return ContractResponse(
        rollover_history=history,
        estimated_charges=float(economics.gross_fee - economics.net_fee),
        net_income=float(economics.net_fee),
        id=view.id,
        stock_id=view.stock_id,
        symbol=view.symbol,
        series=view.series,
        trade_date=view.trade_date,
        expiry_date=view.expiry_date,
        reverse_leg_date=view.reverse_leg_date,
        quantity_lent=view.quantity_lent,
        lending_fee=view.lending_fee,
        stored_status=view.stored_status,
        effective_status=view.effective_status,
        days_remaining=view.days_remaining,
        tenure_days=view.tenure_days,
        lending_fee_income=view.lending_fee_income,
        is_rollover_candidate=view.is_rollover_candidate,
        notes=view.notes,
        origin_date=view.origin_date,
        rolled_from_id=view.rolled_from_id,
        series_code=view.series_code,
        closed_on=view.closed_on,
        chain_days=view.chain_days,
    )


def _act(db: Session, action, contract_id: int, **kwargs) -> ContractResponse:
    """Run a lifecycle action; map errors: unknown 404, wrong status 409, invalid 422."""
    try:
        contract = action(db, contract_id, **kwargs)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ContractStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return _to_response(ContractService.get_contract_view(db=db, contract_id=contract.id))


@router.get("", response_model=list[ContractResponse])
def list_contracts(
    effective_status: Optional[str] = Query(
        None,
        description="Filter by derived status: ACTIVE, EXPIRING_SOON, EXPIRED, RECALLED, CLOSED, ROLLED_OVER",
    ),
    db: Session = Depends(get_db),
) -> list[ContractResponse]:
    """List lending contracts with derived lifecycle state (nearest expiry first)."""
    views = ContractService.list_contracts(db=db, effective_status=effective_status)
    return [_to_response(v, db) for v in views]


@router.post("", response_model=ContractResponse, status_code=status.HTTP_201_CREATED)
def create_contract(
    payload: ContractCreate,
    db: Session = Depends(get_db),
) -> ContractResponse:
    """Open a lending contract against an existing portfolio stock."""
    try:
        contract, warning = ContractService.create_contract_with_warning(
            db=db,
            stock_id=payload.stock_id,
            expiry_date=payload.expiry_date,
            quantity_lent=payload.quantity_lent,
            lending_fee=payload.lending_fee,
            series=payload.series,
            trade_date=payload.trade_date,
            reverse_leg_date=payload.reverse_leg_date,
            notes=payload.notes,
            series_code=payload.series_code,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )
    view = ContractService.get_contract_view(db=db, contract_id=contract.id)
    return _to_response(view).model_copy(update={"warning": warning})


@router.get("/{contract_id}", response_model=ContractResponse)
def get_contract(
    contract_id: int,
    db: Session = Depends(get_db),
) -> ContractResponse:
    """Get a single contract with derived lifecycle state."""
    view = ContractService.get_contract_view(db=db, contract_id=contract_id)
    if view is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Contract with ID {contract_id} not found.",
        )
    return _to_response(view)


@router.post("/{contract_id}/close", response_model=ContractResponse)
def close_contract(contract_id: int, db: Session = Depends(get_db)) -> ContractResponse:
    """Close the contract and book its P&L once (idempotent; a second close changes nothing)."""
    return _act(db, ContractService.close_contract, contract_id)


@router.post("/{contract_id}/recall", response_model=ContractResponse)
def recall_contract(contract_id: int, db: Session = Depends(get_db)) -> ContractResponse:
    """Mark the contract RECALLED (shares called back early); no P&L is booked."""
    return _act(db, ContractService.recall_contract, contract_id)


@router.post("/{contract_id}/roll", response_model=ContractResponse, status_code=status.HTTP_201_CREATED)
def roll_contract(contract_id: int, payload: ContractRoll, db: Session = Depends(get_db)) -> ContractResponse:
    """Roll into a later series: the old contract becomes ROLLED_OVER and the new
    ACTIVE one (returned) keeps the original origin_date. 422 past SEBI's 365-day cap."""
    return _act(
        db,
        ContractService.rollover_contract,
        contract_id,
        new_expiry_date=payload.expiry_date,
        new_lending_fee=payload.lending_fee,
        new_series=payload.series,
        new_series_code=payload.series_code,
        new_reverse_leg_date=payload.reverse_leg_date,
        quantity_lent=payload.quantity_lent,
        trade_date=payload.trade_date,
        notes=payload.notes,
    )


@router.patch("/{contract_id}", response_model=ContractResponse)
def update_contract(contract_id: int, payload: ContractUpdate, db: Session = Depends(get_db)) -> ContractResponse:
    """Correct notes, fee, series, dates. A booked P&L is re-booked, never duplicated."""
    return _act(db, ContractService.update_contract, contract_id, **payload.model_dump(exclude_unset=True))


@router.get("/{contract_id}/roll-options", response_model=RolloverRecommendationResponse)
def roll_options(contract_id: int, db: Session = Depends(get_db)) -> RolloverRecommendationResponse:
    """Later Series B options for one open contract, priced at the bid, with the
    365-day cap and 'N of 365 days used' (pre-fills the roll dialog)."""
    try:
        rec = RolloverService.advise_contract(db, contract_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ContractStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return rollover_to_response(rec)
