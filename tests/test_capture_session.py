"""Fix 1a: capture_nse_fixtures.py --session writes only to its own folder.

NSE is mocked; nothing here calls the network. Real captures are run by hand
in market hours.
"""

from __future__ import annotations

import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from slbm_tracker.services.contract_service import IST
from slbm_tracker.services.nse_client import NSEBlockedError

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "capture_nse_fixtures.py"
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "nse" / "getquote_slbm_IRFC.json").read_text())
NOW = datetime.datetime(2026, 10, 5, 10, 0, tzinfo=IST)


def _load():
    spec = importlib.util.spec_from_file_location("capture_nse_fixtures", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshot_tree(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


@pytest.fixture()
def out_root(tmp_path: Path) -> Path:
    root = tmp_path / "nse"
    root.mkdir()
    # Stand-ins for the existing fixture files that must never change.
    (root / "getquote_slbm_IRFC.json").write_text('{"existing": true}\n')
    (root / "MANIFEST.json").write_text('{"existing": true}\n')
    return root


def _client(side_effect=None) -> AsyncMock:
    client = AsyncMock()
    client.request.side_effect = side_effect or (lambda url, params=None, retries=None: FIXTURE)
    return client


async def test_session_writes_only_inside_its_own_folder(out_root: Path):
    capture = _load()
    before = _snapshot_tree(out_root)
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    client = _client()
    folder = await capture.run_session(
        ["IRFC", "SBIN", "NTPC"], gap_minutes=5, out_root=out_root, client=client,
        sleep=fake_sleep, now=lambda: NOW,
    )

    assert folder == out_root / "session-2026-10-05"
    after = _snapshot_tree(out_root)
    assert {k: v for k, v in after.items() if not k.startswith("session-2026-10-05/")} == before
    files = sorted(p.name for p in folder.iterdir())
    assert files == ["MANIFEST.json"] + sorted(
        f"quote_{s}_{n}.json" for s in ("IRFC", "NTPC", "SBIN") for n in (1, 2)
    )
    assert sleeps == [300]
    assert all(c.kwargs["retries"] == 0 for c in client.request.await_args_list)
    manifest = json.loads((folder / "MANIFEST.json").read_text())
    assert manifest["gap_minutes"] == 5 and manifest["symbols"] == ["IRFC", "SBIN", "NTPC"]
    assert manifest["files"]["quote_SBIN_2.json"]["capture"] == 2
    assert manifest["synthetic"] is False


async def test_a_second_session_the_same_day_gets_a_new_folder(out_root: Path):
    capture = _load()
    async def no_sleep(_): pass
    first = await capture.run_session(["IRFC"], gap_minutes=5, out_root=out_root, client=_client(),
                                      sleep=no_sleep, now=lambda: NOW)
    snapshot = _snapshot_tree(first)
    second = await capture.run_session(["IRFC"], gap_minutes=5, out_root=out_root, client=_client(),
                                       sleep=no_sleep, now=lambda: NOW + datetime.timedelta(hours=1))
    assert second != first and second.name == "session-2026-10-05-1100"
    assert _snapshot_tree(first) == snapshot


async def test_a_403_stops_the_session_and_is_recorded(out_root: Path):
    capture = _load()
    calls = []

    def respond(url, params=None, retries=None):
        calls.append(params["symbol"])
        if params["symbol"] == "SBIN":
            raise NSEBlockedError("403")
        return FIXTURE

    async def no_sleep(_): pass
    folder = await capture.run_session(["IRFC", "SBIN", "NTPC"], gap_minutes=5, out_root=out_root,
                                       client=_client(respond), sleep=no_sleep, now=lambda: NOW)
    assert calls == ["IRFC", "SBIN"]  # stopped at the block: no NTPC, no second capture
    manifest = json.loads((folder / "MANIFEST.json").read_text())
    assert "403" in manifest["stopped"]
    assert sorted(p.name for p in folder.iterdir()) == ["MANIFEST.json", "quote_IRFC_1.json"]


async def test_irfc_is_always_included_once(out_root: Path):
    capture = _load()
    assert capture.session_symbols(["sbin", "IRFC", "ntpc", "SBIN"]) == ["IRFC", "SBIN", "NTPC"]


def test_plain_mode_refuses_to_overwrite_existing_fixtures(out_root: Path):
    capture = _load()
    with pytest.raises(SystemExit):
        capture.check_plain_targets(out_root, overwrite=False)
    capture.check_plain_targets(out_root, overwrite=True)  # explicit opt-in
