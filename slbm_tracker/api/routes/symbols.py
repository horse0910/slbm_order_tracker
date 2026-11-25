"""Tracked SLB symbol-universe API endpoints.

Exposes the configured/seeded symbol universe (see
:mod:`slbm_tracker.services.symbol_universe_service`). This is distinct from:

* ``/api/portfolio`` — actual holdings (quantity, average price, ISIN).
* ``/api/slb/eligible`` — a LIVE NSE fetch of exchange-eligible securities.

The frontend uses this endpoint to populate symbol selectors for SLB
monitoring without requiring the user to first own the symbol or depend on a
live NSE round-trip.
"""

from __future__ import annotations

import logging
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from slbm_tracker.api.schemas import SymbolUniverseResponse, TrackedSymbolResponse
from slbm_tracker.db.session import get_db
from slbm_tracker.services.symbol_universe_service import SymbolUniverseService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/symbols", tags=["Symbol Universe"])


@router.get("", response_model=SymbolUniverseResponse)
def list_tracked_symbols(
    db: Session = Depends(get_db),
) -> SymbolUniverseResponse:
    """Return the configured/seeded SLB tracked-symbol universe (no market data)."""
    symbols = SymbolUniverseService.list_symbols(db=db)
    return SymbolUniverseResponse(
        count=len(symbols),
        symbols=[TrackedSymbolResponse.model_validate(s) for s in symbols],
    )
