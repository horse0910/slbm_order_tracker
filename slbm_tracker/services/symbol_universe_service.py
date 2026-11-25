"""Tracked SLB symbol-universe seeding and retrieval.

The *symbol universe* is the configured list of symbols the application
monitors for SLB opportunities and offers in symbol selectors. It is a
deliberately thin concept:

* It is NOT the portfolio. A tracked symbol carries no quantity, average price,
  ISIN, or any market data — only the ticker. Owning a symbol (a
  :class:`~slbm_tracker.db.models.Stock`) and tracking it are independent.
* It is NOT NSE's live eligibility feed. ``/api/slb/eligible`` still performs a
  live NSE fetch of exchange-eligible securities; that endpoint's semantics are
  intentionally left unchanged. This universe is a local, configurable seed
  list that is stable offline and available before any NSE call succeeds.

Seeding is idempotent: running it any number of times converges the table to
exactly the configured set without creating duplicates.
"""

from __future__ import annotations

import logging
from sqlalchemy.orm import Session

from slbm_tracker.db.models import TrackedSymbol

logger = logging.getLogger(__name__)


# The initial SLB tracked-symbol universe, preserved exactly as provided.
# These are ticker symbols only — no holdings, prices, or market data are
# implied or fabricated by inclusion here.
SEED_SYMBOLS: tuple[str, ...] = (
    "ARCIL",
    "AXISBANK",
    "BANKBARODA",
    "BEL",
    "BHEL",
    "CANBK",
    "CDSL",
    "COALINDIA",
    "COCHINSHIP",
    "CONCOR",
    "DWARKESH",
    "EDELWEISS",
    "EVINDIA",
    "GREAVESCOT",
    "HAL",
    "HCC",
    "HDFCBANK",
    "HEROMOTOCO",
    "INDHOTEL",
    "INFY",
    "IRFC",
    "JIOFIN",
    "JUNIORBEES",
    "KOTAKBANK",
    "LCCPROJECT",
    "LT",
    "MAZDOCK",
    "MID150BEES",
    "MID150CASE",
    "MON100",
    "MUTHOOTFIN",
    "NIFTYBEES",
    "NTPC",
    "OLAELEC",
    "ONGC",
    "RELIANCE",
    "RVNL",
    "SAIL",
    "SBIN",
    "SUZLON",
    "TATACHEM",
    "TATACONSUM",
    "TATAPOWER",
    "TATASTEEL",
    "TATATECH",
    "TCS",
    "TECHM",
    "TITAN",
    "TMCV",
    "TMPV",
    "TOP100CASE",
    "VAML",
    "VEDL",
    "VEDPOWER",
    "VISL",
    "VOGL",
    "VOLTAS",
    "YESBANK",
)


class SymbolUniverseService:
    """Read/seed operations for the tracked SLB symbol universe."""

    @staticmethod
    def seed_symbols(
        db: Session, symbols: tuple[str, ...] | list[str] = SEED_SYMBOLS
    ) -> int:
        """Idempotently ensure every symbol in ``symbols`` exists in the universe.

        Only symbols not already present are inserted, so repeated runs never
        create duplicates. In-list repeats are also collapsed. Returns the count
        of newly inserted rows (0 when the universe is already fully seeded).
        """
        existing = {row[0] for row in db.query(TrackedSymbol.symbol).all()}
        seen = set(existing)
        added = 0
        for raw in symbols:
            sym = raw.strip().upper()
            if not sym or sym in seen:
                continue
            db.add(TrackedSymbol(symbol=sym))
            seen.add(sym)
            added += 1

        if added:
            db.commit()
            logger.info("Seeded %s new tracked symbol(s) into the universe.", added)
        return added

    @staticmethod
    def list_symbols(db: Session) -> list[TrackedSymbol]:
        """Return all tracked symbols, ordered alphabetically for stable UIs."""
        return (
            db.query(TrackedSymbol)
            .order_by(TrackedSymbol.symbol.asc())
            .all()
        )
