"""Settings API — read + edit the non-secret business preferences (Phase 5D).

Exposes ONLY the whitelisted, non-secret business preferences that drive the
contract, alert and P&L engines, so the frontend can both show and edit which
rates/thresholds are in effect. Credentials/secrets are never exposed or
accepted here: SMTP credentials and every other secret live in environment
variables only, are excluded from :class:`SettingsResponse`, and are not fields
of :class:`SettingsUpdate` (which forbids extra keys) — so they can neither be
read nor written through this API.

Effective values layer a persisted per-key override (``app_settings`` table) on
top of the environment/.env base; see
:mod:`slbm_tracker.services.business_config` for the resolver and its whitelist.
GET reflects the persisted state, PUT applies a validated partial update, and
DELETE reverts every override to the environment defaults.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from slbm_tracker.api.schemas import SettingsResponse, SettingsUpdate
from slbm_tracker.db.session import get_db
from slbm_tracker.services import business_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["Settings"])


@router.get("", response_model=SettingsResponse)
def get_business_settings(db: Session = Depends(get_db)) -> SettingsResponse:
    """Return the effective non-secret business configuration.

    Reloads the override cache from the DB first so the response always reflects
    the persisted state, even on a fresh process where startup refresh was
    skipped.
    """
    business_config.refresh(db)
    return SettingsResponse(**business_config.get_effective_dict())


@router.put("", response_model=SettingsResponse)
def update_business_settings(
    payload: SettingsUpdate, db: Session = Depends(get_db)
) -> SettingsResponse:
    """Apply a validated partial update and return the new effective config.

    Only the fields present in the request are changed; validation is
    all-or-nothing (one bad value rejects the whole batch with 422 and persists
    nothing).
    """
    try:
        effective = business_config.apply_updates(
            db, payload.model_dump(exclude_unset=True)
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return SettingsResponse(**effective)


@router.delete("", response_model=SettingsResponse)
def reset_business_settings(db: Session = Depends(get_db)) -> SettingsResponse:
    """Reset every override to the environment default; return effective config."""
    return SettingsResponse(**business_config.reset_all(db))
