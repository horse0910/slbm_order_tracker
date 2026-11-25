"""Effective business-preference resolver (Phase 5D editable settings).

Layers a small set of USER-EDITABLE, NON-SECRET business preferences on top of
the environment/.env configuration exposed by
:func:`slbm_tracker.config.get_settings`.

Design
------
``get_settings()`` remains the immutable ENV/base layer (an ``@lru_cache``
singleton). This module keeps a SEPARATE in-process ``_overrides`` cache holding
ONLY the whitelisted keys that have a persisted row in ``app_settings``. Reads go
through :func:`effective` (override-if-present-else-env), so the base layer is
never mutated: the pure, DB-free P&L math in
:meth:`PnLService.compute_breakdown` and the existing service tests keep seeing
env defaults whenever no override is set.

The whitelist (:data:`EDITABLE_SETTINGS`) is the SINGLE source of truth for what
may be edited and within what bounds. Anything not listed here — every SMTP
credential and other secret above all — can never be written, loaded, or
returned, even if a row were hand-inserted into ``app_settings``.

Values persist JSON-encoded so a bool/int/float round-trips to its exact type.
The cache is swapped atomically (a fresh dict rebound to the module global) so a
concurrent reader on the scheduler thread never observes a half-populated map.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from slbm_tracker.config import get_settings
from slbm_tracker.db.models import AppSetting

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Spec:
    """Type + bounds for one editable key (bounds validate USER input)."""

    kind: str  # "int" | "float" | "bool"
    minimum: float | None = None
    maximum: float | None = None


# Ordered whitelist of the ONLY editable, non-secret business preferences.
# Bounds mirror (and, where the env Field is unbounded, sensibly tighten for
# user input) the constraints in slbm_tracker.config.Settings. SMTP_* and every
# other secret are intentionally absent and thus unwritable/unreadable here.
EDITABLE_SETTINGS: dict[str, _Spec] = {
    # Contracts
    "expiring_soon_days": _Spec("int", 1, 60),
    # Alerts
    "default_alert_yield_threshold": _Spec("float", 0.0, None),
    "tight_spread_threshold_pct": _Spec("float", 0.0, None),
    "above_average_yield_margin_pct": _Spec("float", 0.0, None),
    "notifications_enabled": _Spec("bool"),
    # Phase 5.4: per-rule cooldown (minutes) on top of edge triggering.
    "alert_cooldown_minutes": _Spec("int", 0, 1440),
    # Phase 5.1: desktop notifications on this Mac.
    "macos_notifications_enabled": _Spec("bool"),
    # Live board (Phase 2.13): seconds between full sweeps in market hours.
    "live_refresh_seconds": _Spec("int", 30, 3600),
    # Opportunities (Phase 3.4). series_family stays env-only.
    "min_net_yield_pct": _Spec("float", 0.0, 100.0),
    "min_fill_qty": _Spec("int", 1, 1_000_000_000),
    "min_net_fee_inr": _Spec("float", 0.0, 1_000_000.0),
    "tracked_expiries": _Spec("int", 1, 6),
    # Rollover (Phase 4.5): warn this many days before the reverse leg.
    "rollover_warn_days": _Spec("int", 1, 60),
    # P&L
    "broker_processing_fee_pct": _Spec("float", 0.0, 100.0),
    "gst_pct": _Spec("float", 0.0, 100.0),
    "dp_charge_flat_inr": _Spec("float", 0.0, 1000.0),
    "default_tax_slab_pct": _Spec("float", 0.0, 100.0),
}

# In-process override cache: {key: coerced value} for keys with a persisted row.
# Rebound atomically by _swap(); never mutated in place.
_overrides: dict[str, Any] = {}


def _swap(new: dict[str, Any]) -> None:
    """Atomically replace the override cache (a single GIL-safe rebind)."""
    global _overrides
    _overrides = new


def reset_cache() -> None:
    """Drop all in-process overrides so env defaults take over.

    Used for test isolation and as the clean process default before the first
    :func:`refresh`.
    """
    _swap({})


def effective(key: str) -> Any:
    """Return the effective value for ``key``.

    The persisted override if one is set, otherwise the environment/.env value
    from :func:`get_settings`. Values are already correctly typed (pydantic types
    the env layer; overrides are coerced on write/load).
    """
    if key in _overrides:
        return _overrides[key]
    return getattr(get_settings(), key)


def get_effective_dict() -> dict[str, Any]:
    """Return every editable key's effective value (no bound re-validation).

    The env layer may legitimately hold a value outside the API's user-input
    bounds; reads surface it as-is rather than erroring, so a settings read can
    never fail on an unusual configured value.
    """
    return {key: effective(key) for key in EDITABLE_SETTINGS}


def _coerce_and_validate(key: str, raw: Any) -> Any:
    """Coerce ``raw`` to the key's declared type and enforce its bounds.

    The single gate every write and every load passes through. Raises
    ``ValueError`` with a user-facing message for an unknown key, a wrong type,
    or an out-of-range value.
    """
    spec = EDITABLE_SETTINGS.get(key)
    if spec is None:
        raise ValueError(f"'{key}' is not an editable setting")

    if spec.kind == "bool":
        if not isinstance(raw, bool):
            raise ValueError(f"{key} must be true or false")
        return raw

    # Reject bool for numeric keys (bool is a subclass of int in Python).
    if isinstance(raw, bool):
        raise ValueError(f"{key} must be a number")

    if spec.kind == "int":
        if isinstance(raw, float) and raw.is_integer():
            value: Any = int(raw)
        elif isinstance(raw, int):
            value = int(raw)
        else:
            raise ValueError(f"{key} must be a whole number")
    else:  # float
        if isinstance(raw, (int, float)):
            value = float(raw)
        else:
            raise ValueError(f"{key} must be a number")

    # NaN/±Infinity would slip past the bounds checks below (NaN compares False
    # to everything; +Inf passes any unbounded maximum). Reject them explicitly
    # on both the write path and the refresh() load path.
    if not math.isfinite(value):
        raise ValueError(f"{key} must be a finite number")

    if spec.minimum is not None and value < spec.minimum:
        raise ValueError(f"{key} must be at least {spec.minimum:g}")
    if spec.maximum is not None and value > spec.maximum:
        raise ValueError(f"{key} must be at most {spec.maximum:g}")
    return value


def refresh(db: Session) -> None:
    """Reload the override cache from the ``app_settings`` table.

    Only whitelisted keys are honored; a row whose stored value fails JSON decode
    or validation is skipped (logged) and that key falls back to its env value,
    so a single bad or tampered row can never break startup or a settings read.
    """
    loaded: dict[str, Any] = {}
    for row in db.query(AppSetting).all():
        if row.key not in EDITABLE_SETTINGS:
            continue  # non-whitelisted / stale / hand-inserted secret — ignore
        try:
            loaded[row.key] = _coerce_and_validate(row.key, json.loads(row.value))
        except (ValueError, TypeError, json.JSONDecodeError):
            logger.warning("Ignoring invalid app_settings override for %r", row.key)
    _swap(loaded)


def apply_updates(db: Session, updates: dict[str, Any]) -> dict[str, Any]:
    """Validate, persist and activate a batch of overrides; return effective config.

    ``updates`` maps key -> new value; a key whose value is ``None`` is ignored
    (no change). EVERY value is validated BEFORE anything is written, so one bad
    value rejects the whole batch (raising ``ValueError``) with nothing
    persisted. After committing, the cache is reloaded from the DB so the DB
    stays the single source of truth.
    """
    coerced: dict[str, Any] = {}
    for key, value in updates.items():
        if value is None:
            continue
        coerced[key] = _coerce_and_validate(key, value)  # ValueError -> 422 upstream

    for key, value in coerced.items():
        row = db.query(AppSetting).filter(AppSetting.key == key).one_or_none()
        if row is None:
            db.add(AppSetting(key=key, value=json.dumps(value)))
        else:
            row.value = json.dumps(value)
    db.commit()
    refresh(db)
    return get_effective_dict()


def reset_all(db: Session) -> dict[str, Any]:
    """Delete every override row (revert to env defaults); return effective config."""
    db.query(AppSetting).delete()
    db.commit()
    refresh(db)
    return get_effective_dict()
