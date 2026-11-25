"""Additive-only schema migration helper (Phase 0.3).

``Base.metadata.create_all`` creates missing tables but never adds a column to
a table that already exists, so a new model column would silently never reach
the user's real database. Every column added after the baseline is therefore
registered in :data:`ADDITIVE_COLUMNS` and applied by :func:`run_migrations`,
which ``init_db()`` calls right after ``create_all``.

Rules (house rule 3):

* Columns are only ever ADDED. Nothing is dropped, renamed or altered; a column
  that already exists is left exactly as it is, whatever DDL is registered.
* Idempotent: a second run finds every column present and changes nothing.
* SQLite ``ALTER TABLE … ADD COLUMN`` cannot add a ``NOT NULL`` column without a
  default, so register new columns as nullable or with a ``DEFAULT``.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# {table: {column: ddl}} for every column added after the baseline schema.
# Append only; never remove an entry once it has shipped.
ADDITIVE_COLUMNS: dict[str, dict[str, str]] = {
    # Phase 1.5: when the last Zerodha holdings import set Stock.quantity.
    # Phase 2.12: when a sweep last set is_slb_eligible from NSE.
    # Phase 3.2: pledged (collateral) shares from the Zerodha import.
    "stocks": {
        "holdings_imported_at": "DATETIME",
        "slb_checked_at": "DATETIME",
        "pledged_quantity": "INTEGER NOT NULL DEFAULT 0",
    },
    # Phase 2.5: what the quote API gives per series (NULL on legacy rows).
    "slb_snapshots": {
        "series_code": "VARCHAR(10)",
        "series_family": "VARCHAR(10)",
        "settlement_date": "DATE",
        "settlement_estimated": "BOOLEAN",
        "best_bid_qty": "INTEGER",
        "best_ask_qty": "INTEGER",
    },
}

# Phase 4.1: contract lifecycle (origin for the 365-day cap, roll chain, code,
# and the day a contract stopped being lent).
ADDITIVE_COLUMNS["lending_contracts"] = {
    "origin_date": "DATE",
    "rolled_from_id": "INTEGER",
    "series_code": "VARCHAR(10)",
    "closed_on": "DATE",
}

# Phase 5: delivery outcome (sent / skipped / failed) beside the delivered flag.
ADDITIVE_COLUMNS["notification_deliveries"] = {"outcome": "VARCHAR(10)"}

# One-time fills for columns added above: (table, column, expression). They only
# ever set rows where the column is still NULL, so a re-run changes nothing.
ADDITIVE_BACKFILLS: list[tuple[str, str, str]] = [
    # A contract recorded before Phase 4 is its own origin.
    ("lending_contracts", "origin_date", "trade_date"),
]

# {table: {index name: [columns]}}; created IF NOT EXISTS, never dropped.
ADDITIVE_INDEXES: dict[str, dict[str, list[str]]] = {
    # Phase 2.5: latest snapshot per (symbol, family, expiry).
    "slb_snapshots": {
        "ix_slb_snapshots_symbol_family_expiry_ts": [
            "symbol", "series_family", "expiry_date", "timestamp",
        ],
    },
}


def _check_identifier(name: str) -> None:
    if not isinstance(name, str) or not _IDENTIFIER.match(name):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")


def ensure_columns(engine: Engine, table: str, columns: dict[str, str]) -> list[str]:
    """Add each column in ``columns`` that ``table`` does not have yet.

    Returns the names of the columns added, in the order given (empty when the
    table already has them all). Raises ``ValueError`` for an unknown table or an
    unsafe identifier.
    """
    _check_identifier(table)
    for name in columns:
        _check_identifier(name)

    added: list[str] = []
    with engine.begin() as conn:
        existing = {row[1] for row in conn.execute(text(f'PRAGMA table_info("{table}")'))}
        if not existing:
            raise ValueError(f"Table {table!r} does not exist; ensure_columns only adds columns.")
        for name, ddl in columns.items():
            if name in existing:
                continue
            conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {ddl}'))
            added.append(name)
    if added:
        logger.info("Migration: added column(s) %s to %s.", ", ".join(added), table)
    return added


def ensure_indexes(engine: Engine, table: str, indexes: dict[str, list[str]]) -> list[str]:
    """Create each named index that does not exist yet. Returns the names created."""
    _check_identifier(table)
    for name, cols in indexes.items():
        _check_identifier(name)
        for col in cols:
            _check_identifier(col)

    created: list[str] = []
    with engine.begin() as conn:
        if not list(conn.execute(text(f'PRAGMA table_info("{table}")'))):
            raise ValueError(f"Table {table!r} does not exist; ensure_indexes only adds indexes.")
        existing = {row[1] for row in conn.execute(text(f'PRAGMA index_list("{table}")'))}
        for name, cols in indexes.items():
            if name in existing:
                continue
            col_sql = ", ".join(f'"{c}"' for c in cols)
            conn.execute(text(f'CREATE INDEX IF NOT EXISTS "{name}" ON "{table}" ({col_sql})'))
            created.append(name)
    if created:
        logger.info("Migration: created index(es) %s on %s.", ", ".join(created), table)
    return created


def run_migrations(engine: Engine) -> dict[str, list[str]]:
    """Apply every registered additive column, then index. Returns {table: added}."""
    added = {
        table: cols
        for table, columns in ADDITIVE_COLUMNS.items()
        if (cols := ensure_columns(engine, table, columns))
    }
    for table, indexes in ADDITIVE_INDEXES.items():
        if names := ensure_indexes(engine, table, indexes):
            added.setdefault(table, []).extend(names)
    for table, column, source in ADDITIVE_BACKFILLS:
        backfill_nulls(engine, table, column, source)
    return added


def backfill_nulls(engine: Engine, table: str, column: str, source_column: str) -> int:
    """Set ``column = source_column`` where ``column`` IS NULL. Returns rows set."""
    for name in (table, column, source_column):
        _check_identifier(name)
    with engine.begin() as conn:
        if not list(conn.execute(text(f'PRAGMA table_info("{table}")'))):
            return 0
        result = conn.execute(
            text(f'UPDATE "{table}" SET "{column}" = "{source_column}" WHERE "{column}" IS NULL')
        )
    if result.rowcount:
        logger.info("Migration: backfilled %s.%s on %s row(s).", table, column, result.rowcount)
    return result.rowcount or 0
