"""Standalone, idempotent seeding of the tracked SLB symbol universe.

Run manually against the configured database:

    python -m slbm_tracker.db.seed

Safe to run repeatedly — it converges the ``tracked_symbols`` table to exactly
the configured universe without creating duplicates. Seeding also runs
automatically on API startup (see ``slbm_tracker.api.app.lifespan``); this
module exists for explicit/manual runs and CI verification.
"""

from __future__ import annotations

import logging

from slbm_tracker.db.session import SessionLocal, init_db
from slbm_tracker.services.symbol_universe_service import (
    SEED_SYMBOLS,
    SymbolUniverseService,
)

logger = logging.getLogger(__name__)


def run() -> int:
    """Ensure schema exists and seed the symbol universe. Returns rows added."""
    init_db()
    db = SessionLocal()
    try:
        added = SymbolUniverseService.seed_symbols(db)
    finally:
        db.close()
    return added


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    inserted = run()
    total = len(SEED_SYMBOLS)
    print(
        f"Symbol universe seed complete: {inserted} new symbol(s) added; "
        f"{total} symbols configured."
    )
