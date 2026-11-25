"""SLB snapshot retention / pruning (Phase 4 hardening).

Phase 4's background scheduler persists a batch of :class:`SLBSnapshot` rows on
every tick, so the ``slb_snapshots`` table would otherwise grow without bound
(~7k rows/day at the default cadence, forever). This service enforces a simple
time-based retention horizon: rows older than ``snapshot_retention_days`` are
deleted.

Safety:

* It is a pure, best-effort *maintenance* step. It touches ONLY ``slb_snapshots``
  — never alerts, contracts, or P&L — so pruning old raw market snapshots can
  never alter any already-computed, already-persisted money (those live in their
  own tables and are derived at write time, not recomputed from old snapshots).
* It is safe against every rule that reads snapshots: ``YIELD_THRESHOLD`` /
  ``TIGHT_SPREAD`` use only the most-recent snapshot per (symbol, expiry) and
  ``ABOVE_AVERAGE`` uses the last ``above_average_lookback_samples`` rows. Any
  reasonable retention horizon keeps far more than those need.
* ``retention_days <= 0`` disables pruning (keep forever).

Timestamps are naive-UTC (matching how snapshots are written), so the cutoff is
computed from :func:`datetime.datetime.utcnow`.
"""

from __future__ import annotations

import datetime
import logging
from typing import Optional

from sqlalchemy.orm import Session

from slbm_tracker.config import get_settings
from slbm_tracker.db.models import SLBSnapshot

logger = logging.getLogger(__name__)


class SnapshotRetentionService:
    """Delete SLB snapshots older than a configurable horizon."""

    @staticmethod
    def prune(
        db: Session,
        *,
        retention_days: Optional[int] = None,
        now: Optional[datetime.datetime] = None,
    ) -> int:
        """Delete snapshots older than the retention horizon; return rows deleted.

        ``retention_days`` defaults to ``settings.snapshot_retention_days``. A
        value ``<= 0`` disables pruning and returns 0 without touching the DB.
        Commits on success. The caller (the scheduler tick) isolates any error,
        so this may raise; it does not swallow DB failures itself.
        """
        days = (
            retention_days
            if retention_days is not None
            else get_settings().snapshot_retention_days
        )
        if days <= 0:
            return 0

        cutoff = (now or datetime.datetime.utcnow()) - datetime.timedelta(days=days)
        deleted = (
            db.query(SLBSnapshot)
            .filter(SLBSnapshot.timestamp < cutoff)
            .delete(synchronize_session=False)
        )
        db.commit()
        if deleted:
            logger.info(
                "Snapshot retention pruned %d row(s) older than %s (%dd horizon).",
                deleted,
                cutoff.isoformat(),
                days,
            )
        return deleted
