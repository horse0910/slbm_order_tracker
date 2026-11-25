"""Unit tests for the Phase 5D editable-settings resolver (business_config).

Covers the whitelist, coercion/validation, atomic batch behavior, persistence,
override-vs-env resolution, refresh hardening (invalid / non-whitelisted rows),
and reset — the guarantees the settings API and the refactored engines rely on.
"""

from __future__ import annotations

import datetime
import json

import pytest
from sqlalchemy.orm import Session

from slbm_tracker.config import get_settings
from slbm_tracker.db.models import AppSetting
from slbm_tracker.services import business_config


def test_effective_falls_back_to_env_when_no_override():
    s = get_settings()
    assert business_config.effective("gst_pct") == s.gst_pct
    assert business_config.effective("expiring_soon_days") == s.expiring_soon_days
    assert set(business_config.get_effective_dict()) == set(business_config.EDITABLE_SETTINGS)


def test_apply_updates_persists_and_takes_effect(db: Session):
    eff = business_config.apply_updates(db, {"gst_pct": 12.5, "expiring_soon_days": 14})
    assert eff["gst_pct"] == 12.5
    assert eff["expiring_soon_days"] == 14
    assert business_config.effective("gst_pct") == 12.5
    # Persisted as a JSON-encoded scalar, one row per key.
    row = db.query(AppSetting).filter(AppSetting.key == "gst_pct").one()
    assert json.loads(row.value) == 12.5


def test_apply_updates_ignores_none(db: Session):
    business_config.apply_updates(db, {"gst_pct": None})
    assert db.query(AppSetting).count() == 0
    assert business_config.effective("gst_pct") == get_settings().gst_pct


def test_validation_rejects_out_of_range_and_is_atomic(db: Session):
    with pytest.raises(ValueError):
        business_config.apply_updates(db, {"expiring_soon_days": 0})
    with pytest.raises(ValueError):
        business_config.apply_updates(db, {"gst_pct": 150})
    # All-or-nothing: a batch with one bad value must persist nothing.
    with pytest.raises(ValueError):
        business_config.apply_updates(db, {"gst_pct": 12.0, "expiring_soon_days": 999})
    assert db.query(AppSetting).count() == 0
    assert business_config.effective("gst_pct") == get_settings().gst_pct


def test_validation_rejects_wrong_types(db: Session):
    with pytest.raises(ValueError):
        business_config.apply_updates(db, {"expiring_soon_days": 7.5})  # not a whole number
    with pytest.raises(ValueError):
        business_config.apply_updates(db, {"notifications_enabled": "yes"})  # not a bool
    with pytest.raises(ValueError):
        business_config.apply_updates(db, {"gst_pct": True})  # bool is not a number
    # None of the rejected writes persisted a row.
    assert db.query(AppSetting).count() == 0


def test_unknown_or_nonwhitelisted_key_rejected(db: Session):
    # A secret and a deliberately env-only pref are both outside the whitelist.
    with pytest.raises(ValueError):
        business_config.apply_updates(db, {"smtp_password": "x"})
    with pytest.raises(ValueError):
        business_config.apply_updates(db, {"above_average_lookback_samples": 5})


def test_reset_all_reverts_to_env(db: Session):
    business_config.apply_updates(db, {"gst_pct": 5.0})
    assert business_config.effective("gst_pct") == 5.0
    eff = business_config.reset_all(db)
    assert eff["gst_pct"] == get_settings().gst_pct
    assert db.query(AppSetting).count() == 0


def test_refresh_ignores_invalid_and_nonwhitelisted_rows(db: Session):
    db.add_all(
        [
            AppSetting(key="expiring_soon_days", value=json.dumps(14)),  # valid
            AppSetting(key="gst_pct", value=json.dumps(500.0)),  # out of range
            AppSetting(key="smtp_password", value=json.dumps("leak")),  # not whitelisted
            AppSetting(key="dp_charge_flat_inr", value="not-json"),  # undecodable
            AppSetting(key="dp_charge_pct", value=json.dumps(0.02)),  # retired key (Phase 1.4)
        ]
    )
    db.commit()
    business_config.refresh(db)
    # Only the valid, whitelisted row is honored; the rest fall back to env.
    assert business_config._overrides.get("expiring_soon_days") == 14
    assert "gst_pct" not in business_config._overrides
    assert "dp_charge_flat_inr" not in business_config._overrides
    assert "dp_charge_pct" not in business_config._overrides
    assert "smtp_password" not in business_config._overrides


def test_notifications_enabled_bool_roundtrip(db: Session):
    business_config.apply_updates(db, {"notifications_enabled": False})
    assert business_config.effective("notifications_enabled") is False
    row = db.query(AppSetting).filter(AppSetting.key == "notifications_enabled").one()
    assert json.loads(row.value) is False  # JSON bool, not 0/"false"


def test_validation_rejects_non_finite_numbers(db: Session):
    # NaN and ±Infinity slip past bare </> bounds checks (NaN compares False to
    # everything; +Inf passes any unbounded maximum), so they must be rejected
    # explicitly. Covers both a bounded key and a max-unbounded one.
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            business_config.apply_updates(db, {"gst_pct": bad})
    with pytest.raises(ValueError):
        business_config.apply_updates(db, {"default_alert_yield_threshold": float("inf")})
    # Nothing persisted; the key still resolves to its env default.
    assert db.query(AppSetting).count() == 0
    assert business_config.effective("gst_pct") == get_settings().gst_pct


def test_refresh_ignores_non_finite_row(db: Session):
    # json.dumps emits the non-standard Infinity token, so a hand-inserted row
    # could smuggle a non-finite value past JSON decode — the coercion gate on
    # the load path must still reject it and fall back to env.
    db.add(AppSetting(key="gst_pct", value=json.dumps(float("inf"))))
    db.commit()
    business_config.refresh(db)
    assert "gst_pct" not in business_config._overrides
    assert business_config.effective("gst_pct") == get_settings().gst_pct


def test_rejected_batch_leaves_prior_override_intact(db: Session):
    # A previously-persisted valid override must survive a later batch that is
    # rejected — the all-or-nothing guarantee protects existing state too, not
    # just an empty starting cache.
    business_config.apply_updates(db, {"gst_pct": 12.0})
    with pytest.raises(ValueError):
        business_config.apply_updates(db, {"gst_pct": 9.0, "expiring_soon_days": 999})
    # The earlier value stands in both the cache and the DB; the bad sibling never lands.
    assert business_config.effective("gst_pct") == 12.0
    assert "expiring_soon_days" not in business_config._overrides
    row = db.query(AppSetting).filter(AppSetting.key == "gst_pct").one()
    assert json.loads(row.value) == 12.0
    assert db.query(AppSetting).filter(AppSetting.key == "expiring_soon_days").count() == 0


def test_bounds_are_inclusive_at_edges(db: Session):
    # The declared min/max are accepted (inclusive); just outside is rejected.
    eff = business_config.apply_updates(db, {"expiring_soon_days": 1, "gst_pct": 0.0})
    assert eff["expiring_soon_days"] == 1 and eff["gst_pct"] == 0.0
    eff = business_config.apply_updates(db, {"expiring_soon_days": 60, "gst_pct": 100.0})
    assert eff["expiring_soon_days"] == 60 and eff["gst_pct"] == 100.0
    with pytest.raises(ValueError):
        business_config.apply_updates(db, {"expiring_soon_days": 61})
    with pytest.raises(ValueError):
        business_config.apply_updates(db, {"gst_pct": 100.01})


def test_none_is_skipped_but_valid_siblings_persist(db: Session):
    # A None value is a no-op even when batched with a real change; the sibling
    # still lands and no row is written for the None key.
    business_config.apply_updates(db, {"gst_pct": None, "expiring_soon_days": 14})
    assert business_config.effective("expiring_soon_days") == 14
    assert business_config.effective("gst_pct") == get_settings().gst_pct
    assert {r.key for r in db.query(AppSetting).all()} == {"expiring_soon_days"}


def test_override_flows_into_pnl_engine_output(db: Session):
    # End-to-end: a persisted override must change the P&L engine's computed
    # output, proving the engine reads through effective() (not a stale
    # get_settings() snapshot).
    from slbm_tracker.services.pnl_service import PnLService

    before = PnLService.compute_breakdown(10000, 0.35)
    assert before.gst > 0  # env default GST applies
    business_config.apply_updates(db, {"gst_pct": 0.0, "default_tax_slab_pct": 0.0})
    after = PnLService.compute_breakdown(10000, 0.35)
    assert after.gst == 0.0  # override zeroes the GST line
    assert after.estimated_tax == 0.0  # and the tax-slab estimate
    assert after.net_income > before.net_income  # removing the GST cost raises net


def test_override_flows_into_contract_status_engine(db: Session):
    # End-to-end: widening the expiring-soon window via an override flips a
    # contract's DERIVED status, proving ContractService reads through effective().
    from slbm_tracker.services.contract_service import (
        STATUS_ACTIVE,
        STATUS_EXPIRING_SOON,
        ContractService,
    )

    today = datetime.date(2026, 10, 1)
    expiry = datetime.date(2026, 10, 16)  # 15 days out
    # Default window (7 days) → 15 days out is still ACTIVE.
    assert ContractService.derive_status(STATUS_ACTIVE, expiry, today=today) == STATUS_ACTIVE
    business_config.apply_updates(db, {"expiring_soon_days": 20})
    # Widened to 20 days now catches it — purely via the override.
    assert (
        ContractService.derive_status(STATUS_ACTIVE, expiry, today=today)
        == STATUS_EXPIRING_SOON
    )
