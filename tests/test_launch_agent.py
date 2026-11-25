"""Phase 7.2: launchd agent scripts. Only the --print mode and syntax are
exercised here; nothing is installed on the machine running the tests."""

from __future__ import annotations

import plistlib
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
INSTALL = REPO / "scripts" / "install_launch_agent.sh"
UNINSTALL = REPO / "scripts" / "uninstall_launch_agent.sh"


@pytest.mark.parametrize("script", [INSTALL, UNINSTALL])
def test_scripts_parse(script: Path):
    assert subprocess.run(["bash", "-n", str(script)], capture_output=True).returncode == 0


def test_print_mode_writes_the_plist_to_stdout_and_touches_nothing(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    out = subprocess.run(
        ["bash", str(INSTALL), "--print"], capture_output=True, text=True, env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
    )
    assert out.returncode == 0, out.stderr
    assert list(home.iterdir()) == []  # nothing created, nothing loaded
    plist = plistlib.loads(out.stdout.encode())
    assert plist["Label"] == "com.harsh.slbm-tracker"
    assert plist["ProgramArguments"] == [
        str(REPO / ".venv" / "bin" / "python"), "-m", "uvicorn", "slbm_tracker.api.app:app",
        "--host", "127.0.0.1", "--port", "8010",
    ]
    assert plist["WorkingDirectory"] == str(REPO)
    assert plist["KeepAlive"] is True and plist["RunAtLoad"] is True
    logs = home / "Library" / "Logs" / "slbm-tracker"
    assert plist["StandardOutPath"] == str(logs / "slbm-tracker.out.log")
    assert plist["StandardErrorPath"] == str(logs / "slbm-tracker.err.log")


@pytest.mark.skipif(shutil.which("plutil") is None, reason="plutil is macOS-only")
def test_printed_plist_passes_plutil_lint(tmp_path: Path):
    out = subprocess.run(["bash", str(INSTALL), "--print"], capture_output=True, text=True,
                         env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"})
    path = tmp_path / "agent.plist"
    path.write_text(out.stdout)
    assert subprocess.run(["plutil", "-lint", str(path)], capture_output=True).returncode == 0
