"""Phase 0.3: additive-only migration helper (``slbm_tracker.db.migrate``).

``Base.metadata.create_all`` never adds a column to a table that already
exists, so every schema change after the baseline goes through
``ensure_columns``: it adds missing columns and never drops or alters one.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from slbm_tracker.db import migrate
from slbm_tracker.db.migrate import ensure_columns
from slbm_tracker.db.session import init_db

REPO_ROOT = Path(__file__).resolve().parent.parent
# The consistent .backup snapshot from Phase 0.2. Never the live database.
REAL_DB_BACKUP = REPO_ROOT / "slbm_tracker.backup.db"


def _engine(path: Path):
    return create_engine(f"sqlite:///{path}")


def _columns(engine, table: str) -> list[tuple]:
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(text(f'PRAGMA table_info("{table}")'))]


def _same_rows_new_tables_empty(after: dict, before: dict) -> bool:
    """Every table that existed keeps its row count; tables added since are empty."""
    return all(after[t] == n for t, n in before.items()) and all(
        after[t] == 0 for t in set(after) - set(before)
    )


def _schema_and_counts(path: Path) -> tuple[list, dict]:
    conn = sqlite3.connect(path)
    try:
        schema = conn.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()
        tables = [n for (t, n, _) in schema if t == "table"]
        counts = {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
        return schema, counts
    finally:
        conn.close()


@pytest.fixture()
def demo_engine(tmp_path):
    eng = _engine(tmp_path / "demo.db")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE demo (id INTEGER PRIMARY KEY, name VARCHAR(20) NOT NULL)"))
        conn.execute(text("INSERT INTO demo (id, name) VALUES (1, 'kept')"))
    yield eng
    eng.dispose()


def test_adds_only_missing_columns(demo_engine):
    added = ensure_columns(
        demo_engine,
        "demo",
        {"name": "VARCHAR(99)", "note": "TEXT", "qty": "INTEGER NOT NULL DEFAULT 0"},
    )
    assert added == ["note", "qty"]
    cols = {c[1]: c for c in _columns(demo_engine, "demo")}
    assert set(cols) == {"id", "name", "note", "qty"}
    # The existing column is not altered, even though a different DDL was passed.
    assert cols["name"][2] == "VARCHAR(20)"
    with demo_engine.connect() as conn:
        assert conn.execute(text("SELECT id, name, note, qty FROM demo")).all() == [(1, "kept", None, 0)]


def test_is_idempotent(demo_engine):
    spec = {"note": "TEXT"}
    assert ensure_columns(demo_engine, "demo", spec) == ["note"]
    before = _columns(demo_engine, "demo")
    assert ensure_columns(demo_engine, "demo", spec) == []
    assert _columns(demo_engine, "demo") == before


def test_never_drops_columns_left_out_of_the_spec(demo_engine):
    ensure_columns(demo_engine, "demo", {})
    assert [c[1] for c in _columns(demo_engine, "demo")] == ["id", "name"]


def test_unknown_table_raises(demo_engine):
    with pytest.raises(ValueError, match="no_such_table"):
        ensure_columns(demo_engine, "no_such_table", {"x": "TEXT"})


@pytest.mark.parametrize("bad", ["bad name", "x;DROP TABLE demo", "", "1abc"])
def test_rejects_unsafe_identifiers(demo_engine, bad):
    with pytest.raises(ValueError):
        ensure_columns(demo_engine, "demo", {bad: "TEXT"})
    with pytest.raises(ValueError):
        ensure_columns(demo_engine, bad, {"ok": "TEXT"})


def test_init_db_applies_registered_columns_once(tmp_path, monkeypatch):
    monkeypatch.setattr(migrate, "ADDITIVE_COLUMNS", {"stocks": {"zz_probe": "TEXT"}})
    eng = _engine(tmp_path / "app.db")
    init_db(eng)
    with eng.begin() as conn:
        conn.execute(text("INSERT INTO stocks (symbol, quantity, avg_buy_price, is_slb_eligible) VALUES ('SBIN', 5, 1.0, 1)"))
    init_db(eng)
    names = [c["name"] for c in inspect(eng).get_columns("stocks")]
    assert names.count("zz_probe") == 1
    with eng.connect() as conn:
        assert conn.execute(text("SELECT symbol, quantity FROM stocks")).all() == [("SBIN", 5)]
    eng.dispose()


@pytest.mark.skipif(not REAL_DB_BACKUP.exists(), reason="no local backup of the real database")
def test_init_db_twice_on_copy_of_real_database_changes_nothing(tmp_path):
    copy = tmp_path / "real_copy.db"
    shutil.copyfile(REAL_DB_BACKUP, copy)
    eng = _engine(copy)
    init_db(eng)
    first = _schema_and_counts(copy)
    init_db(eng)
    eng.dispose()
    assert _schema_and_counts(copy) == first


def test_suite_never_uses_the_live_database():
    from slbm_tracker.config import get_settings
    from slbm_tracker.db.session import engine

    live = (REPO_ROOT / "slbm_tracker.db").resolve()
    assert engine.url.database is not None
    assert Path(engine.url.database).resolve() != live
    assert "slbm-tests-" in get_settings().database_url


@pytest.mark.skipif(not REAL_DB_BACKUP.exists(), reason="no local backup of the real database")
def test_real_database_copy_gains_holdings_imported_at_without_losing_rows(tmp_path):
    copy = tmp_path / "real_copy.db"
    shutil.copyfile(REAL_DB_BACKUP, copy)
    _, before_counts = _schema_and_counts(copy)
    eng = _engine(copy)
    init_db(eng)
    cols = {c["name"]: c for c in inspect(eng).get_columns("stocks")}
    with eng.connect() as conn:
        nulls = conn.execute(text("SELECT COUNT(*) FROM stocks WHERE holdings_imported_at IS NULL")).scalar()
    eng.dispose()
    assert "holdings_imported_at" in cols and cols["holdings_imported_at"]["nullable"]
    assert _same_rows_new_tables_empty(_schema_and_counts(copy)[1], before_counts)
    assert nulls == before_counts["stocks"]


# --- Phase 2.5: snapshot columns and the family index ----------------------

PRE_PHASE1_BACKUP = REPO_ROOT / "slbm_tracker.pre-phase1.backup.db"
SNAPSHOT_COLUMNS = {"series_code", "series_family", "settlement_date", "settlement_estimated",
                    "best_bid_qty", "best_ask_qty"}
FAMILY_INDEX = "ix_slb_snapshots_symbol_family_expiry_ts"


def _indexes(engine, table: str) -> dict[str, list[str]]:
    with engine.connect() as conn:
        names = [r[1] for r in conn.execute(text(f'PRAGMA index_list("{table}")'))]
        return {n: [r[2] for r in conn.execute(text(f'PRAGMA index_info("{n}")'))] for n in names}


def test_ensure_indexes_is_additive_and_idempotent(demo_engine):
    from slbm_tracker.db.migrate import ensure_indexes

    assert ensure_indexes(demo_engine, "demo", {"ix_demo_name": ["name", "id"]}) == ["ix_demo_name"]
    assert ensure_indexes(demo_engine, "demo", {"ix_demo_name": ["name", "id"]}) == []
    assert _indexes(demo_engine, "demo")["ix_demo_name"] == ["name", "id"]
    with pytest.raises(ValueError):
        ensure_indexes(demo_engine, "demo", {"ix bad": ["name"]})
    with pytest.raises(ValueError):
        ensure_indexes(demo_engine, "no_such_table", {"ix_x": ["name"]})


def test_fresh_database_has_snapshot_columns_and_family_index(tmp_path):
    eng = _engine(tmp_path / "fresh.db")
    init_db(eng)
    cols = {c["name"] for c in inspect(eng).get_columns("slb_snapshots")}
    assert SNAPSHOT_COLUMNS <= cols
    assert _indexes(eng, "slb_snapshots")[FAMILY_INDEX] == ["symbol", "series_family", "expiry_date", "timestamp"]
    eng.dispose()


@pytest.mark.skipif(not PRE_PHASE1_BACKUP.exists(), reason="no local pre-phase-1 backup")
def test_pre_phase1_backup_copy_migrates_once_and_keeps_every_row(tmp_path):
    copy = tmp_path / "pre_phase1_copy.db"
    shutil.copyfile(PRE_PHASE1_BACKUP, copy)
    _, before_counts = _schema_and_counts(copy)
    eng = _engine(copy)
    init_db(eng)
    first = _schema_and_counts(copy)
    cols = {c["name"]: c for c in inspect(eng).get_columns("slb_snapshots")}
    assert all(cols[c]["nullable"] for c in SNAPSHOT_COLUMNS)
    assert {"holdings_imported_at", "slb_checked_at"} <= {c["name"] for c in inspect(eng).get_columns("stocks")}
    assert FAMILY_INDEX in _indexes(eng, "slb_snapshots")
    with eng.connect() as conn:
        legacy = conn.execute(text("SELECT COUNT(*) FROM slb_snapshots WHERE series_family IS NULL")).scalar()
    init_db(eng)
    eng.dispose()
    assert _same_rows_new_tables_empty(first[1], before_counts)  # no row lost or added
    assert legacy == before_counts["slb_snapshots"]  # legacy rows have no family
    assert _schema_and_counts(copy) == first    # second run changes nothing


# --- Phase 4.1: contract lifecycle columns, origin_date backfilled ----------

CONTRACT_COLUMNS = {"origin_date", "rolled_from_id", "series_code", "closed_on"}


def test_origin_date_backfill_fills_only_missing_values(tmp_path):
    eng = _engine(tmp_path / "contracts.db")
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE lending_contracts (id INTEGER PRIMARY KEY, trade_date DATE NOT NULL)"))
        conn.execute(text("INSERT INTO lending_contracts (id, trade_date) VALUES (1, '2026-09-29'), (2, '2026-10-01')"))
    init_db(eng)
    with eng.begin() as conn:
        conn.execute(text("UPDATE lending_contracts SET origin_date = '2026-01-15' WHERE id = 2"))
    init_db(eng)  # second run must not overwrite an origin already set
    with eng.connect() as conn:
        rows = conn.execute(text("SELECT id, origin_date FROM lending_contracts ORDER BY id")).all()
    eng.dispose()
    assert rows == [(1, "2026-09-29"), (2, "2026-01-15")]


@pytest.mark.skipif(not PRE_PHASE1_BACKUP.exists(), reason="no local pre-phase-1 backup")
def test_pre_phase1_copy_gains_contract_lifecycle_columns(tmp_path):
    copy = tmp_path / "pre_phase1_contracts.db"
    shutil.copyfile(PRE_PHASE1_BACKUP, copy)
    eng = _engine(copy)
    init_db(eng)
    cols = {c["name"]: c for c in inspect(eng).get_columns("lending_contracts")}
    eng.dispose()
    assert CONTRACT_COLUMNS <= set(cols) and all(cols[c]["nullable"] for c in CONTRACT_COLUMNS)
