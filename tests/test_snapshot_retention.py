"""Tests for SLB snapshot retention pruning (Phase 4 hardening).

Offline and deterministic: a fixed ``now`` is injected so the cutoff never
depends on the machine clock. Retention touches only ``slb_snapshots``.
"""

from __future__ import annotations

import datetime

from sqlalchemy.orm import Session

from slbm_tracker.config import get_settings
from slbm_tracker.db.models import SLBSnapshot
from slbm_tracker.services.snapshot_retention_service import SnapshotRetentionService


def _snap(db: Session, *, ts: datetime.datetime, symbol: str = "RELIANCE") -> None:
    db.add(
        SLBSnapshot(
            symbol=symbol,
            expiry_date=datetime.date(2026, 11, 3),
            annualized_yield=8.0,
            timestamp=ts,
        )
    )
    db.commit()


def test_prune_deletes_only_rows_older_than_horizon(db: Session):
    now = datetime.datetime(2026, 9, 24, 12, 0, 0)
    old = now - datetime.timedelta(days=120)  # beyond the 90d horizon
    edge = now - datetime.timedelta(days=89)  # just inside the horizon (kept)
    fresh = now - datetime.timedelta(hours=1)
    _snap(db, ts=old)
    _snap(db, ts=edge)
    _snap(db, ts=fresh)

    deleted = SnapshotRetentionService.prune(db, retention_days=90, now=now)

    assert deleted == 1
    remaining = {s.timestamp for s in db.query(SLBSnapshot).all()}
    assert old not in remaining
    assert edge in remaining and fresh in remaining


def test_prune_disabled_when_retention_days_zero(db: Session):
    """0 = keep forever: nothing is deleted even for an ancient row."""
    now = datetime.datetime(2026, 9, 24, 12, 0, 0)
    _snap(db, ts=now - datetime.timedelta(days=3650))
    deleted = SnapshotRetentionService.prune(db, retention_days=0, now=now)
    assert deleted == 0
    assert db.query(SLBSnapshot).count() == 1


def test_prune_defaults_to_configured_horizon(db: Session, monkeypatch):
    """With no explicit retention_days, the configured setting is used."""
    monkeypatch.setattr(get_settings(), "snapshot_retention_days", 30)
    now = datetime.datetime(2026, 9, 24, 12, 0, 0)
    _snap(db, ts=now - datetime.timedelta(days=31))  # older than 30d -> pruned
    _snap(db, ts=now - datetime.timedelta(days=29))  # within 30d -> kept
    deleted = SnapshotRetentionService.prune(db, now=now)
    assert deleted == 1
    assert db.query(SLBSnapshot).count() == 1


def test_prune_no_rows_is_a_safe_noop(db: Session):
    deleted = SnapshotRetentionService.prune(db, retention_days=90)
    assert deleted == 0
