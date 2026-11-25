"""Capture real NSE SLB responses into tests/fixtures/nse/.

Run on the Mac, where NSE is reachable. Two modes:

* Session (market hours; for 2.13's trade events):

      .venv/bin/python scripts/capture_nse_fixtures.py --session --symbols SBIN NTPC --gap-minutes 5

  Captures the GetQuoteApi quote for IRFC plus the given symbols, waits
  ``--gap-minutes`` (default 5), captures them again, and writes everything to
  a new folder ``session-YYYY-MM-DD/`` (IST date; ``-HHMM`` added if that folder
  exists) with its own MANIFEST.json. It never writes anywhere else.

* Plain (Phase 0.4; the base IRFC set): symbol view, quote, 30-day history and
  series master into the fixtures folder itself. Refuses to overwrite existing
  fixture files (tests depend on them) unless ``--overwrite`` is given.

Uses A's own NSEClient (session warming, browser headers, rate-limit delay),
with retries=0 so a block (403) stops the run at once. Responses are saved as
NSE returned them (re-serialised JSON, same content). An empty response is
reported and NOT saved: fixtures must be real payloads, never stand-ins.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from slbm_tracker.config import get_settings  # noqa: E402
from slbm_tracker.services.contract_service import IST, today_ist  # noqa: E402
from slbm_tracker.services.nse_client import NSEBlockedError, NSEClient, NSEError  # noqa: E402

SYMBOL = "IRFC"
HISTORY_DAYS = 30
OUT_DIR = REPO_ROOT / "tests" / "fixtures" / "nse"


def _now_ist() -> datetime.datetime:
    return datetime.datetime.now(IST)


def _is_empty(payload: Any) -> bool:
    if payload in (None, "", [], {}):
        return True
    if isinstance(payload, dict) and "data" in payload and payload["data"] in (None, [], {}):
        return True
    return False


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- session mode


def session_symbols(extra: list[str]) -> list[str]:
    """IRFC first, then the given symbols, upper-cased and without repeats."""
    out = [SYMBOL]
    for sym in extra:
        sym = sym.strip().upper()
        if sym and sym not in out:
            out.append(sym)
    return out


def _session_folder(out_root: Path, now: datetime.datetime) -> Path:
    folder = out_root / f"session-{now:%Y-%m-%d}"
    if folder.exists():
        folder = out_root / f"session-{now:%Y-%m-%d-%H%M}"
    if folder.exists():
        raise SystemExit(f"{folder} already exists; wait a minute and run again.")
    return folder


async def run_session(
    symbols: list[str],
    *,
    gap_minutes: float = 5,
    out_root: Path = OUT_DIR,
    client: Optional[NSEClient] = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    now: Callable[[], datetime.datetime] = _now_ist,
) -> Path:
    """Two quote captures per symbol, ``gap_minutes`` apart, into a new session folder."""
    symbols = session_symbols(symbols)
    folder = _session_folder(out_root, now())
    folder.mkdir(parents=True)
    own_client = client is None
    client = client or NSEClient()
    url = get_settings().nse_quote_api_url
    manifest: dict[str, Any] = {
        "synthetic": False,
        "purpose": "Market-hours quote captures for trade-event tests (2.13): two per symbol.",
        "symbols": symbols,
        "gap_minutes": gap_minutes,
        "url": url,
        "files": {},
        "problems": [],
        "stopped": None,
    }
    try:
        for capture in (1, 2):
            if capture == 2:
                await sleep(gap_minutes * 60)
            for sym in symbols:
                params = {"functionName": "getGetQuoteSLBMData", "symbol": sym, "series": "", "month": ""}
                name = f"quote_{sym}_{capture}.json"
                try:
                    payload = await client.request(url, params=params, retries=0)
                except NSEBlockedError as exc:
                    manifest["stopped"] = f"{name}: blocked (403), session stopped: {exc}"
                    return folder
                except NSEError as exc:
                    manifest["problems"].append(f"{name}: {type(exc).__name__}: {exc}")
                    continue
                if _is_empty(payload):
                    manifest["problems"].append(f"{name}: empty response, not saved")
                    continue
                _write_json(folder / name, payload)
                manifest["files"][name] = {
                    "symbol": sym,
                    "capture": capture,
                    "params": params,
                    "captured_at_ist": now().isoformat(timespec="seconds"),
                }
                print(f"saved {folder.name}/{name}")
    finally:
        _write_json(folder / "MANIFEST.json", manifest)
        if own_client:
            await client.close()
    return folder


# ------------------------------------------------------------------ plain mode


def _requests() -> list[tuple[str, str, dict[str, str] | None]]:
    """(file name, url, params) for every base fixture."""
    s = get_settings()
    to_date = today_ist()
    from_date = to_date - datetime.timedelta(days=HISTORY_DAYS)
    return [
        ("live_analysis_slb_symbol_IRFC.json", s.nse_slb_api_url, {"symbol": SYMBOL}),
        (
            "getquote_slbm_IRFC.json",
            s.nse_quote_api_url,
            {"functionName": "getGetQuoteSLBMData", "symbol": SYMBOL, "series": "", "month": ""},
        ),
        (
            "slb_history_IRFC.json",
            s.nse_quote_api_url,
            {
                "functionName": "getSLBHistoricalData",
                "symbol": SYMBOL,
                "fromDate": from_date.strftime("%d-%m-%Y"),
                "toDate": to_date.strftime("%d-%m-%Y"),
                "csv": "false",
            },
        ),
        ("series_master.json", f"{s.nse_base_url}/api/live-analysis-slb-series-master", None),
    ]


def check_plain_targets(out_root: Path, overwrite: bool) -> None:
    """Refuse to replace fixture files the tests depend on, unless asked to."""
    existing = [name for name, _, _ in _requests() if (out_root / name).exists()]
    if existing and not overwrite:
        raise SystemExit(
            "Refusing to overwrite existing fixtures the tests depend on: "
            + ", ".join(existing)
            + ". Use --session for new captures, or --overwrite if you really mean it."
        )


async def run_plain(out_root: Path = OUT_DIR) -> int:
    out_root.mkdir(parents=True, exist_ok=True)
    client = NSEClient()
    manifest: dict[str, Any] = {}
    problems: list[str] = []
    try:
        for name, url, params in _requests():
            try:
                payload = await client.request(url, params=params, retries=0)
            except NSEBlockedError as exc:
                problems.append(f"{name}: blocked (403), stopping: {exc}")
                break
            except NSEError as exc:
                problems.append(f"{name}: {type(exc).__name__}: {exc}")
                continue
            if _is_empty(payload):
                problems.append(f"{name}: empty response, not saved")
                continue
            _write_json(out_root / name, payload)
            manifest[name] = {
                "url": url,
                "params": params,
                "captured_at_ist": _now_ist().isoformat(timespec="seconds"),
            }
            print(f"saved {name}")
    finally:
        await client.close()

    if manifest:
        path = out_root / "MANIFEST.json"
        existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        existing.update(manifest)
        _write_json(path, existing)
    for line in problems:
        print(f"PROBLEM {line}", file=sys.stderr)
    return 1 if problems else 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Capture real NSE SLB responses into tests/fixtures/nse/.")
    parser.add_argument("--session", action="store_true", help="two quote captures into a new session folder")
    parser.add_argument("--symbols", nargs="*", default=[], help="symbols besides IRFC (session mode)")
    parser.add_argument("--gap-minutes", type=float, default=5, help="minutes between the two captures")
    parser.add_argument("--overwrite", action="store_true", help="plain mode: replace existing fixtures")
    args = parser.parse_args(argv)

    if args.session:
        folder = asyncio.run(run_session(args.symbols, gap_minutes=args.gap_minutes))
        manifest = json.loads((folder / "MANIFEST.json").read_text())
        for line in manifest["problems"] + ([manifest["stopped"]] if manifest["stopped"] else []):
            print(f"PROBLEM {line}", file=sys.stderr)
        print(f"session folder: {folder}")
        return 1 if manifest["problems"] or manifest["stopped"] else 0

    check_plain_targets(OUT_DIR, args.overwrite)
    return asyncio.run(run_plain())


if __name__ == "__main__":
    sys.exit(main())
