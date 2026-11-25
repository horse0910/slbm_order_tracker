"""Daily database backup after market close (Phase 7.4).

Uses SQLite's online backup API (the same as ``sqlite3 db ".backup ..."``), so
the copy includes writes still in the WAL file and is consistent while the app
keeps running. One file per IST day, ``slbm_tracker-YYYY-MM-DD.db``, in
``backup_dir`` (default ``~/Library/Application Support/slbm-tracker/backups``);
the newest ``backup_keep`` (14) are kept. Each backup is checked: it must pass
``PRAGMA integrity_check`` and have the same row count in every table.
"""

from __future__ import annotations

import datetime
import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional

from slbm_tracker.db.session import engine

logger = logging.getLogger(__name__)

PREFIX = "slbm_tracker-"


def database_file() -> Optional[Path]:
    """The app's SQLite file, or None when the database is not a file."""
    if engine.url.get_backend_name() != "sqlite":
        return None
    name = engine.url.database
    if not name or name == ":memory:":
        return None
    return Path(name).resolve()


def backup_path(dest_dir: Path, day: datetime.date) -> Path:
    return Path(dest_dir).expanduser() / f"{PREFIX}{day.isoformat()}.db"


def _row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}


def run_backup(source: Path, dest_dir: Path, *, keep: int = 14, day: datetime.date) -> dict[str, Any]:
    """Back ``source`` up to ``dest_dir`` for ``day`` (once), verify it, prune to ``keep``.

    Raises RuntimeError if the copy fails verification (the bad file is removed).
    """
    dest_dir = Path(dest_dir).expanduser()
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = backup_path(dest_dir, day)
    created = False
    if not target.exists():
        partial = target.with_suffix(".db.partial")
        src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        dst = sqlite3.connect(partial)
        try:
            src.backup(dst)
            # A standalone backup must open read-only without creating WAL sidecars.
            dst.execute("PRAGMA journal_mode=DELETE")
            src_counts = _row_counts(src)
            ok = dst.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            dst_counts = _row_counts(dst)
        finally:
            dst.close()
            src.close()
        if not ok or dst_counts != src_counts:
            partial.unlink(missing_ok=True)
            raise RuntimeError(f"Backup of {source} failed verification; nothing kept.")
        partial.replace(target)
        created = True
        logger.info("Backed up the database to %s (%s rows).", target, sum(dst_counts.values()))

    backups = sorted(dest_dir.glob(f"{PREFIX}????-??-??.db"))
    pruned = backups[: max(len(backups) - keep, 0)]
    for old in pruned:
        old.unlink()
    if pruned:
        logger.info("Removed %s old backup(s); keeping %s.", len(pruned), keep)
    return {"path": target, "created": created, "pruned": [p.name for p in pruned]}
