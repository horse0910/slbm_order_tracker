"""Phase 7.4: daily database backup after market close, keeping the last 14.

Always on temp databases and temp folders; never the live database or the
real backup folder.
"""

from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from slbm_tracker.config import Settings, get_settings
from slbm_tracker.services import backup_service, scheduler_service
from slbm_tracker.services.contract_service import IST
from slbm_tracker.services.scheduler_service import AlertSchedulerService

DAY = datetime.date(2026, 10, 5)


@pytest.fixture()
def source(tmp_path: Path) -> Path:
    path = tmp_path / "live-like.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE stocks (id INTEGER PRIMARY KEY, symbol TEXT)")
    conn.execute("CREATE TABLE slb_snapshots (id INTEGER PRIMARY KEY, symbol TEXT)")
    conn.executemany("INSERT INTO stocks (symbol) VALUES (?)", [("SBIN",), ("IRFC",), ("INFY",)])
    conn.executemany("INSERT INTO slb_snapshots (symbol) VALUES (?)", [("IRFC",)] * 25)
    conn.commit()
    conn.close()
    return path


def _counts(path: Path) -> dict[str, int]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
    finally:
        conn.close()


def test_backup_opens_and_has_the_same_row_counts(source: Path, tmp_path: Path):
    result = backup_service.run_backup(source, tmp_path / "backups", day=DAY)
    path = result["path"]
    assert path.name == "slbm_tracker-2026-10-05.db"
    assert _counts(path) == _counts(source) == {"stocks": 3, "slb_snapshots": 25}
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()


def test_only_the_newest_14_are_kept(source: Path, tmp_path: Path):
    dest = tmp_path / "backups"
    dest.mkdir()
    for i in range(16):
        (dest / f"slbm_tracker-2026-09-{i + 1:02d}.db").write_bytes(b"old")
    (dest / "notes.txt").write_text("not a backup")
    result = backup_service.run_backup(source, dest, keep=14, day=DAY)
    names = sorted(p.name for p in dest.glob("slbm_tracker-*.db"))
    assert len(names) == 14 and names[-1] == "slbm_tracker-2026-10-05.db"
    assert names[0] == "slbm_tracker-2026-09-04.db"  # the three oldest went
    assert len(result["pruned"]) == 3
    assert (dest / "notes.txt").exists()


def test_one_backup_per_day(source: Path, tmp_path: Path):
    first = backup_service.run_backup(source, tmp_path / "b", day=DAY)
    stamp = first["path"].stat().st_mtime_ns
    second = backup_service.run_backup(source, tmp_path / "b", day=DAY)
    assert second["created"] is False and second["path"].stat().st_mtime_ns == stamp


def test_the_suite_never_backs_up_by_default():
    s = get_settings()
    assert s.backups_enabled is False and "slbm-tests-" in s.backup_dir


async def test_scheduler_backs_up_once_after_the_close(source: Path, tmp_path: Path, db, monkeypatch):
    monkeypatch.setattr(db, "close", lambda: None)
    monkeypatch.setattr(scheduler_service, "SessionLocal", lambda: db)

    async def no_broadcast(_):
        return 0

    monkeypatch.setattr(scheduler_service.ws_manager, "broadcast", no_broadcast)
    monkeypatch.setattr(backup_service, "database_file", lambda: source)
    s = Settings()
    s.scheduler_refresh_market_data = False
    s.backups_enabled = True
    s.backup_dir = str(tmp_path / "backups")
    at = {"t": datetime.datetime(2026, 10, 5, 16, 0, tzinfo=IST)}
    sched = AlertSchedulerService(settings=s, nse_client=AsyncMock(), clock=lambda: at["t"])

    assert (await sched.run_one_tick())["backup"] is None          # before 17:20 IST
    at["t"] = datetime.datetime(2026, 10, 5, 17, 30, tzinfo=IST)
    first = await sched.run_one_tick()
    assert first["backup"].endswith("slbm_tracker-2026-10-05.db")
    at["t"] = datetime.datetime(2026, 10, 5, 18, 0, tzinfo=IST)
    assert (await sched.run_one_tick())["backup"] is None          # already done today
    assert len(list((tmp_path / "backups").glob("*.db"))) == 1
