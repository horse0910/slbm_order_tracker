"""Tests for the Phase 4 background scheduler (AlertSchedulerService).

Offline and deterministic: ``run_one_tick()`` is called directly (never the
sleeping ``_loop``); NSE fetches and ``ws_manager.broadcast`` are monkeypatched;
the scheduler is handed the in-memory test session. These verify the
evaluate -> deliver -> broadcast orchestration, per-symbol/tick error isolation,
the real ``alert_fired`` payload, and the start/stop lifecycle.
"""

from __future__ import annotations

import asyncio
import datetime
import threading
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.orm import Session

from slbm_tracker.config import Settings
from slbm_tracker.db.models import LendingContract, SLBSnapshot, Stock, TrackedSymbol
from slbm_tracker.services import scheduler_service
from slbm_tracker.services.alert_service import AlertService
from slbm_tracker.services.contract_service import IST
from slbm_tracker.services.scheduler_service import AlertSchedulerService, is_market_hours

pytestmark = pytest.mark.usefixtures("pinned_snapshot_today")

# Monday 5 Oct 2026, 11:00 IST: inside market hours.
IN_HOURS = datetime.datetime(2026, 10, 5, 11, 0, tzinfo=IST)


def _at(moment: datetime.datetime):
    return lambda: moment


def _seed_firing_yield_alert(db: Session) -> None:
    """A RELIANCE snapshot at 9% + a YIELD_THRESHOLD>=8% rule -> fires once."""
    db.add(
        SLBSnapshot(series_family="B", 
            symbol="RELIANCE",
            series="Nov-2026",
            expiry_date=datetime.date(2026, 11, 3),
            annualized_yield=9.0,
            timestamp=datetime.datetime.utcnow(),
        )
    )
    db.commit()
    AlertService.create_alert(db, alert_type="YIELD_THRESHOLD", threshold=8.0)


def _use_test_session(db: Session, monkeypatch) -> None:
    """Point the scheduler's SessionLocal at the test session.

    run_one_tick opens SessionLocal() and closes it in ``finally``; neutralize
    close() so the fixture session survives for post-tick assertions.
    """
    monkeypatch.setattr(db, "close", lambda: None)
    monkeypatch.setattr(scheduler_service, "SessionLocal", lambda: db)


def _no_refresh_settings() -> Settings:
    s = Settings()
    s.scheduler_refresh_market_data = False
    return s


def _capture_broadcasts(monkeypatch) -> list[dict]:
    captured: list[dict] = []

    async def fake_broadcast(message):
        captured.append(message)
        return 1

    monkeypatch.setattr(scheduler_service.ws_manager, "broadcast", fake_broadcast)
    return captured


async def test_tick_fires_delivers_and_broadcasts_real_payload(db, monkeypatch):
    _seed_firing_yield_alert(db)
    _use_test_session(db, monkeypatch)
    broadcasts = _capture_broadcasts(monkeypatch)

    sched = AlertSchedulerService(settings=_no_refresh_settings(), nse_client=AsyncMock())
    summary = await sched.run_one_tick()

    assert summary["ok"] is True
    assert summary["evaluated"] == 1
    assert summary["fired"] == 1
    assert summary["market_refreshed"] == 0

    types = [m["type"] for m in broadcasts]
    assert "alert_fired" in types
    assert "scheduler_tick" in types

    fired = next(m for m in broadcasts if m["type"] == "alert_fired")
    assert fired["alert_type"] == "YIELD_THRESHOLD"
    assert fired["message"] and "yield" in fired["message"].lower()
    assert fired["last_triggered_at"] is not None
    assert not fired["last_triggered_at"].endswith("Z")  # naive-UTC ISO

    tick = next(m for m in broadcasts if m["type"] == "scheduler_tick")
    assert tick["evaluated"] == 1 and tick["fired"] == 1


async def test_tick_heartbeat_without_any_fires(db, monkeypatch):
    # No snapshots, no rules -> nothing fires, but the tick still heartbeats.
    _use_test_session(db, monkeypatch)
    broadcasts = _capture_broadcasts(monkeypatch)

    sched = AlertSchedulerService(settings=_no_refresh_settings(), nse_client=AsyncMock())
    summary = await sched.run_one_tick()

    assert summary["ok"] is True
    assert summary["fired"] == 0
    assert [m["type"] for m in broadcasts] == ["scheduler_tick"]


async def test_tick_broadcast_false_pushes_nothing(db, monkeypatch):
    _seed_firing_yield_alert(db)
    _use_test_session(db, monkeypatch)
    broadcasts = _capture_broadcasts(monkeypatch)

    sched = AlertSchedulerService(settings=_no_refresh_settings(), nse_client=AsyncMock())
    summary = await sched.run_one_tick(broadcast=False)

    assert summary["fired"] == 1
    assert broadcasts == []


async def test_refresh_isolates_one_failing_symbol(db, monkeypatch):
    _use_test_session(db, monkeypatch)
    _capture_broadcasts(monkeypatch)

    for sym in ("AAA", "BBB"):
        db.add(Stock(symbol=sym, quantity=10))
    db.commit()

    async def fake_fetch(self, symbol, *args, **kwargs):
        if symbol == "AAA":
            raise RuntimeError("NSE boom")
        return _quote(symbol)

    monkeypatch.setattr(scheduler_service.NSEService, "get_slb_quote", fake_fetch)

    s = Settings()
    s.scheduler_refresh_market_data = True
    sched = AlertSchedulerService(settings=s, nse_client=AsyncMock(), clock=_at(IN_HOURS))
    summary = await sched.run_one_tick()

    # AAA raised but was isolated; BBB still refreshed; the tick completed.
    assert summary["ok"] is True
    assert summary["market_refreshed"] == 1


async def test_tick_isolates_evaluate_failure(db, monkeypatch):
    _use_test_session(db, monkeypatch)

    def boom(*args, **kwargs):
        raise RuntimeError("evaluate exploded")

    monkeypatch.setattr(
        scheduler_service.AlertService, "evaluate_all", staticmethod(boom)
    )

    sched = AlertSchedulerService(settings=_no_refresh_settings(), nse_client=AsyncMock())
    summary = await sched.run_one_tick()

    # A bad tick is caught and reported, never raised (loop stays alive).
    assert summary["ok"] is False
    assert "evaluate exploded" in summary["error"]


async def test_tick_runs_blocking_body_off_the_loop_thread(db, monkeypatch):
    """The evaluate/deliver/prune body (whose delivery step may block on SMTP)
    must run in a worker thread, never the event-loop thread — otherwise a slow
    SMTP send would stall /api/ws and HTTP."""
    _seed_firing_yield_alert(db)
    _use_test_session(db, monkeypatch)
    _capture_broadcasts(monkeypatch)

    loop_thread = threading.get_ident()
    seen: dict[str, int] = {}

    def capture_deliver(*args, **kwargs):
        seen["deliver_thread"] = threading.get_ident()
        return []

    monkeypatch.setattr(
        scheduler_service.AlertDeliveryService, "deliver", staticmethod(capture_deliver)
    )

    sched = AlertSchedulerService(settings=_no_refresh_settings(), nse_client=AsyncMock())
    summary = await sched.run_one_tick()

    assert summary["ok"] is True
    assert summary["evaluated"] == 1 and summary["fired"] == 1
    # Delivery ran, and on a different thread than the event loop — proving the
    # blocking body was offloaded and can't stall the loop.
    assert "deliver_thread" in seen
    assert seen["deliver_thread"] != loop_thread


async def test_start_and_stop_lifecycle(monkeypatch):
    s = Settings()
    s.alert_scheduler_startup_delay_seconds = 0
    s.alert_eval_interval_seconds = 15
    fake_client = AsyncMock()  # close() is awaitable

    sched = AlertSchedulerService(settings=s, nse_client=fake_client)

    ticks = 0

    async def fast_tick(*args, **kwargs):
        nonlocal ticks
        ticks += 1
        return {"ok": True}

    monkeypatch.setattr(sched, "run_one_tick", fast_tick)

    sched.start(asyncio.get_running_loop())
    assert sched.is_running is True
    # start() is idempotent while running (no second task).
    first_task = sched._task
    sched.start(asyncio.get_running_loop())
    assert sched._task is first_task

    await asyncio.sleep(0.05)  # let at least one tick run before the interval sleep
    await sched.stop()

    assert sched.is_running is False
    assert ticks >= 1
    fake_client.close.assert_awaited_once()


async def test_build_alert_fired_message_shape():
    class _Alert:
        id = 7
        alert_type = "TIGHT_SPREAD"
        stock_id = 3
        trigger_message = "spread tight"
        last_triggered_at = datetime.datetime(2026, 9, 24, 10, 30, 0)

    msg = AlertSchedulerService.build_alert_fired_message(_Alert())
    assert msg == {
        "type": "alert_fired",
        "alert_id": 7,
        "alert_type": "TIGHT_SPREAD",
        "stock_id": 3,
        "message": "spread tight",
        "last_triggered_at": "2026-09-24T10:30:00",
    }


async def test_build_alert_fired_message_handles_null_timestamp():
    class _Alert:
        id = 1
        alert_type = "ROLLOVER"
        stock_id = None
        trigger_message = None
        last_triggered_at = None

    msg = AlertSchedulerService.build_alert_fired_message(_Alert())
    assert msg["last_triggered_at"] is None
    assert msg["stock_id"] is None


async def test_tick_prunes_snapshots_beyond_retention_horizon(db, monkeypatch):
    """Each tick enforces snapshot retention so continuous ingestion can't grow
    slb_snapshots without bound."""
    _use_test_session(db, monkeypatch)
    _capture_broadcasts(monkeypatch)

    now = datetime.datetime.utcnow()
    db.add(  # ancient -> beyond the horizon -> pruned
        SLBSnapshot(series_family="B", 
            symbol="RELIANCE",
            expiry_date=datetime.date(2026, 11, 3),
            annualized_yield=8.0,
            timestamp=now - datetime.timedelta(days=200),
        )
    )
    db.add(  # fresh -> kept
        SLBSnapshot(series_family="B", 
            symbol="RELIANCE",
            expiry_date=datetime.date(2026, 11, 3),
            annualized_yield=8.0,
            timestamp=now,
        )
    )
    db.commit()

    s = _no_refresh_settings()
    s.snapshot_retention_days = 90
    sched = AlertSchedulerService(settings=s, nse_client=AsyncMock())
    summary = await sched.run_one_tick()

    assert summary["ok"] is True
    assert summary["pruned"] == 1
    assert db.query(SLBSnapshot).count() == 1


async def test_tick_retention_disabled_prunes_nothing(db, monkeypatch):
    """snapshot_retention_days=0 keeps everything forever."""
    _use_test_session(db, monkeypatch)
    _capture_broadcasts(monkeypatch)

    now = datetime.datetime.utcnow()
    db.add(
        SLBSnapshot(series_family="B", 
            symbol="RELIANCE",
            expiry_date=datetime.date(2026, 11, 3),
            annualized_yield=8.0,
            timestamp=now - datetime.timedelta(days=3650),
        )
    )
    db.commit()

    s = _no_refresh_settings()
    s.snapshot_retention_days = 0
    sched = AlertSchedulerService(settings=s, nse_client=AsyncMock())
    summary = await sched.run_one_tick()

    assert summary["pruned"] == 0
    assert db.query(SLBSnapshot).count() == 1


# --- Phase 1.1: poll held ∪ ACTIVE-lent with a rotating cursor -------------


def _quote(symbol: str, series: list | None = None) -> dict:
    """The shape NSEService.get_slb_quote returns (one live Series B row by default)."""
    if series is None:
        series = [{"series_code": "XN", "series_family": "B", "is_active": True}]
    return {"symbol": symbol, "source": "quote", "blocked": False, "series": series}


def _recording_fetch(monkeypatch) -> list[str]:
    fetched: list[str] = []

    async def fake_fetch(self, symbol, *args, **kwargs):
        fetched.append(symbol)
        return _quote(symbol)

    monkeypatch.setattr(scheduler_service.NSEService, "get_slb_quote", fake_fetch)
    return fetched


def _refresh_settings(cap: int) -> Settings:
    s = Settings()
    s.scheduler_refresh_market_data = True
    s.scheduler_max_symbols_per_tick = cap
    return s


async def test_capped_ticks_rotate_through_every_holding(db, monkeypatch):
    symbols = [f"S{i:02d}" for i in range(58)]
    for sym in symbols:
        db.add(Stock(symbol=sym, quantity=1))
    db.commit()
    _use_test_session(db, monkeypatch)
    _capture_broadcasts(monkeypatch)
    fetched = _recording_fetch(monkeypatch)

    sched = AlertSchedulerService(settings=_refresh_settings(25), nse_client=AsyncMock(), clock=_at(IN_HOURS))
    per_tick = []
    for _ in range(3):
        before = len(fetched)
        await sched.run_one_tick()
        per_tick.append(fetched[before:])

    assert [len(t) for t in per_tick] == [25, 25, 25]
    assert set(fetched) == set(symbols)
    # The second tick starts where the first stopped, not at the top again.
    assert per_tick[1][0] == "S25"


async def test_new_holding_is_polled_without_reseeding(db, monkeypatch):
    db.add(Stock(symbol="AAA", quantity=1))
    db.commit()
    _use_test_session(db, monkeypatch)
    _capture_broadcasts(monkeypatch)
    fetched = _recording_fetch(monkeypatch)
    sched = AlertSchedulerService(settings=_refresh_settings(0), nse_client=AsyncMock(), clock=_at(IN_HOURS))

    await sched.run_one_tick()
    assert fetched == ["AAA"]

    db.add(Stock(symbol="NEWCO", quantity=40))
    db.commit()
    fetched.clear()
    await sched.run_one_tick()
    assert sorted(fetched) == ["AAA", "NEWCO"]


async def test_polls_held_and_lent_but_never_tracked_only_or_sold(db, monkeypatch):
    db.add(Stock(symbol="HELD", quantity=5))
    sold = Stock(symbol="SOLD", quantity=0)
    lent = Stock(symbol="LENT", quantity=0)
    closed = Stock(symbol="CLOSEDLEND", quantity=0)
    db.add_all([sold, lent, closed])
    db.add(TrackedSymbol(symbol="ONLYTRACKED"))
    db.flush()
    db.add(LendingContract(stock_id=lent.id, expiry_date=datetime.date(2026, 11, 3),
                           quantity_lent=100, lending_fee=1.0, status="ACTIVE",
                           trade_date=datetime.date(2026, 10, 1)))
    db.add(LendingContract(stock_id=closed.id, expiry_date=datetime.date(2026, 11, 3),
                           quantity_lent=100, lending_fee=1.0, status="CLOSED",
                           trade_date=datetime.date(2026, 10, 1)))
    db.commit()
    _use_test_session(db, monkeypatch)
    _capture_broadcasts(monkeypatch)
    fetched = _recording_fetch(monkeypatch)

    sched = AlertSchedulerService(settings=_refresh_settings(0), nse_client=AsyncMock(), clock=_at(IN_HOURS))
    await sched.run_one_tick()

    assert sorted(fetched) == ["HELD", "LENT"]


# --- Phase 1.2: NSE calls only 09:00-17:15 IST, Monday to Friday -----------


@pytest.mark.parametrize(
    "moment, expected",
    [
        (datetime.datetime(2026, 10, 5, 9, 0, tzinfo=IST), True),     # Mon open
        (datetime.datetime(2026, 10, 5, 17, 15, tzinfo=IST), True),   # Mon close
        (datetime.datetime(2026, 10, 5, 8, 59, tzinfo=IST), False),
        (datetime.datetime(2026, 10, 5, 17, 16, tzinfo=IST), False),
        (datetime.datetime(2026, 10, 9, 12, 0, tzinfo=IST), True),    # Fri
        (datetime.datetime(2026, 10, 10, 12, 0, tzinfo=IST), False),  # Sat
        (datetime.datetime(2026, 10, 4, 12, 0, tzinfo=IST), False),   # Sun
        # 04:00 UTC Monday is 09:30 IST: compared in IST, not UTC.
        (datetime.datetime(2026, 10, 5, 4, 0, tzinfo=datetime.timezone.utc), True),
        # 20:00 UTC Friday is 01:30 IST Saturday.
        (datetime.datetime(2026, 10, 9, 20, 0, tzinfo=datetime.timezone.utc), False),
    ],
)
def test_is_market_hours(moment, expected):
    assert is_market_hours(moment) is expected


def test_is_market_hours_rejects_naive_datetimes():
    with pytest.raises(ValueError):
        is_market_hours(datetime.datetime(2026, 10, 5, 11, 0))


@pytest.mark.parametrize(
    "moment",
    [
        datetime.datetime(2026, 10, 5, 23, 0, tzinfo=IST),   # weekday night
        datetime.datetime(2026, 10, 10, 11, 0, tzinfo=IST),  # Saturday
    ],
)
async def test_tick_outside_market_hours_makes_zero_nse_requests(db, monkeypatch, moment):
    db.add(Stock(symbol="HELD", quantity=5))
    db.commit()
    _seed_firing_yield_alert(db)
    _use_test_session(db, monkeypatch)
    _capture_broadcasts(monkeypatch)
    fetched = _recording_fetch(monkeypatch)
    client = AsyncMock()

    sched = AlertSchedulerService(settings=_refresh_settings(0), nse_client=client, clock=_at(moment))
    summary = await sched.run_one_tick()

    assert fetched == []
    assert [c for c in client.mock_calls if not c[0].startswith("__")] == []
    assert summary["market_refreshed"] == 0
    assert summary["market_open"] is False
    # Alert evaluation still runs on cached snapshots.
    assert summary["evaluated"] == 1 and summary["fired"] == 1


async def test_market_hours_gate_can_be_switched_off(db, monkeypatch):
    db.add(Stock(symbol="HELD", quantity=5))
    db.commit()
    _use_test_session(db, monkeypatch)
    _capture_broadcasts(monkeypatch)
    fetched = _recording_fetch(monkeypatch)
    s = _refresh_settings(0)
    s.scheduler_market_hours_only = False

    night = datetime.datetime(2026, 10, 5, 23, 0, tzinfo=IST)
    sched = AlertSchedulerService(settings=s, nse_client=AsyncMock(), clock=_at(night))
    await sched.run_one_tick()

    assert fetched == ["HELD"]


async def test_tick_inside_market_hours_polls(db, monkeypatch):
    db.add(Stock(symbol="HELD", quantity=5))
    db.commit()
    _use_test_session(db, monkeypatch)
    _capture_broadcasts(monkeypatch)
    fetched = _recording_fetch(monkeypatch)

    sched = AlertSchedulerService(settings=_refresh_settings(0), nse_client=AsyncMock(), clock=_at(IN_HOURS))
    summary = await sched.run_one_tick()

    assert fetched == ["HELD"]
    assert summary["market_open"] is True


# --- Phase 2.7: one quote request per symbol; stop and back off on a 403 ---


def _client_serving(responses: dict) -> AsyncMock:
    """A mocked NSEClient whose fetch_slb_quote answers (or raises) per symbol."""
    client = AsyncMock()

    async def quote(symbol):
        value = responses[symbol]
        if isinstance(value, Exception):
            raise value
        return value

    client.fetch_slb_quote.side_effect = quote
    return client


def _irfc_payload(symbol: str) -> dict:
    import json
    from pathlib import Path

    raw = json.loads((Path(__file__).parent / "fixtures" / "nse" / "getquote_slbm_IRFC.json").read_text())
    for row in raw["data"]:
        row["symbol"] = symbol
    return raw


async def test_sweep_makes_one_quote_request_per_symbol_and_no_symbol_view_calls(db, monkeypatch):
    for sym in ("AAA", "BBB"):
        db.add(Stock(symbol=sym, quantity=10))
    db.commit()
    _use_test_session(db, monkeypatch)
    _capture_broadcasts(monkeypatch)
    client = _client_serving({"AAA": _irfc_payload("AAA"), "BBB": _irfc_payload("BBB")})

    sched = AlertSchedulerService(settings=_refresh_settings(0), nse_client=client, clock=_at(IN_HOURS))
    summary = await sched.run_one_tick()

    assert [c.args[0] for c in client.fetch_slb_quote.await_args_list] == ["AAA", "BBB"]
    client.fetch_slb_by_symbol.assert_not_called()
    assert summary["market_refreshed"] == 2
    assert db.query(SLBSnapshot).filter_by(symbol="BBB", series_code="XN", series_family="B").count() == 1


async def test_403_stops_the_sweep_and_backs_off_15_minutes(db, monkeypatch):
    from slbm_tracker.services.nse_client import NSEBlockedError

    for sym in ("AAA", "BBB", "CCC"):
        db.add(Stock(symbol=sym, quantity=10))
    db.commit()
    _use_test_session(db, monkeypatch)
    _capture_broadcasts(monkeypatch)
    client = _client_serving({"AAA": _irfc_payload("AAA"), "BBB": NSEBlockedError("403"), "CCC": _irfc_payload("CCC")})
    now = {"t": IN_HOURS}
    sched = AlertSchedulerService(settings=_refresh_settings(0), nse_client=client, clock=lambda: now["t"])

    summary = await sched.run_one_tick()
    assert [c.args[0] for c in client.fetch_slb_quote.await_args_list] == ["AAA", "BBB"]  # CCC not tried
    assert summary["nse_blocked"] is True
    assert summary["nse_backoff_until"] == (IN_HOURS + datetime.timedelta(minutes=15)).isoformat()
    assert summary["evaluated"] == 0 and summary["ok"] is True  # alerts still evaluated (none defined)

    now["t"] = IN_HOURS + datetime.timedelta(minutes=14)
    client.fetch_slb_quote.reset_mock()
    summary = await sched.run_one_tick()
    assert client.fetch_slb_quote.await_count == 0
    assert summary["market_refreshed"] == 0

    now["t"] = IN_HOURS + datetime.timedelta(minutes=15, seconds=1)
    client.fetch_slb_quote.side_effect = lambda s: _irfc_payload(s)
    summary = await sched.run_one_tick()
    assert client.fetch_slb_quote.await_count == 3
    assert summary["nse_blocked"] is False


async def test_other_nse_errors_skip_only_that_symbol(db, monkeypatch):
    from slbm_tracker.services.nse_client import NSEConnectionError

    for sym in ("AAA", "BBB"):
        db.add(Stock(symbol=sym, quantity=10))
    db.commit()
    _use_test_session(db, monkeypatch)
    _capture_broadcasts(monkeypatch)
    client = _client_serving({"AAA": NSEConnectionError("timeout"), "BBB": _irfc_payload("BBB")})

    sched = AlertSchedulerService(settings=_refresh_settings(0), nse_client=client, clock=_at(IN_HOURS))
    summary = await sched.run_one_tick()

    assert summary["market_refreshed"] == 1
    client.fetch_slb_by_symbol.assert_not_called()  # no fallback request inside a sweep


# --- Phase 2.12: eligibility from whether NSE returned any series ---------


async def test_sweep_sets_eligibility_from_the_quote(db, monkeypatch):
    for sym in ("ONSLB", "NOTSLB"):
        db.add(Stock(symbol=sym, quantity=10))
    db.commit()
    _use_test_session(db, monkeypatch)
    _capture_broadcasts(monkeypatch)
    empty = {"data": [], "timestamp": [], "outPosDate": []}
    client = _client_serving({"ONSLB": _irfc_payload("ONSLB"), "NOTSLB": empty})

    sched = AlertSchedulerService(settings=_refresh_settings(0), nse_client=client, clock=_at(IN_HOURS))
    await sched.run_one_tick()

    stocks = {s.symbol: s for s in db.query(Stock).all()}
    assert stocks["ONSLB"].is_slb_eligible is True
    assert stocks["NOTSLB"].is_slb_eligible is False
    expected = IN_HOURS.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    assert stocks["NOTSLB"].slb_checked_at == stocks["ONSLB"].slb_checked_at == expected


async def test_failed_fetch_leaves_eligibility_unchanged(db, monkeypatch):
    from slbm_tracker.services.nse_client import NSEConnectionError

    db.add(Stock(symbol="AAA", quantity=10, is_slb_eligible=True))
    db.commit()
    _use_test_session(db, monkeypatch)
    _capture_broadcasts(monkeypatch)
    client = _client_serving({"AAA": NSEConnectionError("timeout")})

    sched = AlertSchedulerService(settings=_refresh_settings(0), nse_client=client, clock=_at(IN_HOURS))
    await sched.run_one_tick()

    stock = db.query(Stock).one()
    assert stock.is_slb_eligible is True and stock.slb_checked_at is None


def test_ineligible_holdings_are_rechecked_weekly_not_every_sweep(db):
    from slbm_tracker.services.portfolio_service import PortfolioService

    now = datetime.datetime(2026, 10, 12, 6, 0)  # naive UTC
    db.add_all([
        Stock(symbol="ELIGIBLE", quantity=5, is_slb_eligible=True, slb_checked_at=now),
        Stock(symbol="RECENT", quantity=5, is_slb_eligible=False, slb_checked_at=now - datetime.timedelta(days=3)),
        Stock(symbol="STALE", quantity=5, is_slb_eligible=False, slb_checked_at=now - datetime.timedelta(days=7)),
        Stock(symbol="NEVER", quantity=5, is_slb_eligible=False, slb_checked_at=None),
    ])
    lent = Stock(symbol="LENTINELIG", quantity=0, is_slb_eligible=False, slb_checked_at=now)
    db.add(lent)
    db.flush()
    db.add(LendingContract(stock_id=lent.id, expiry_date=datetime.date(2026, 11, 3), quantity_lent=10,
                           lending_fee=1.0, status="ACTIVE", trade_date=datetime.date(2026, 10, 1)))
    db.commit()

    assert PortfolioService.polled_symbols(db, now=now) == ["ELIGIBLE", "LENTINELIG", "NEVER", "STALE"]


# --- Phase 2.13: live sweep cadence, full coverage, overrun, market.trade --


def test_loop_interval_is_live_refresh_in_market_hours_else_alert_interval(monkeypatch):
    from slbm_tracker.services import business_config

    s = Settings()
    s.alert_eval_interval_seconds = 300
    sched = AlertSchedulerService(settings=s, nse_client=AsyncMock())
    monkeypatch.setattr(business_config, "_overrides", {"live_refresh_seconds": 120})
    assert sched.next_interval(IN_HOURS) == 120
    night = datetime.datetime(2026, 10, 5, 23, 0, tzinfo=IST)
    assert sched.next_interval(night) == 300


async def test_default_sweep_covers_every_polled_symbol(db, monkeypatch):
    symbols = [f"S{i:02d}" for i in range(60)]
    for sym in symbols:
        db.add(Stock(symbol=sym, quantity=1))
    db.commit()
    _use_test_session(db, monkeypatch)
    _capture_broadcasts(monkeypatch)
    fetched = _recording_fetch(monkeypatch)

    s = Settings()  # default cap: none
    s.scheduler_refresh_market_data = True
    s.nse_rate_limit_delay_seconds = 2.0
    sched = AlertSchedulerService(settings=s, nse_client=AsyncMock(), clock=_at(IN_HOURS))
    summary = await sched.run_one_tick()

    assert sorted(fetched) == symbols
    assert summary["sweep"]["symbols"] == 60 and summary["sweep"]["fetched"] == 60
    assert summary["sweep"]["warning"] is None  # 60 × 2 s = 120 s fits in 180 s


async def test_sweep_that_cannot_finish_in_time_is_warned_not_capped(db, monkeypatch, caplog):
    for i in range(100):
        db.add(Stock(symbol=f"S{i:03d}", quantity=1))
    db.commit()
    _use_test_session(db, monkeypatch)
    _capture_broadcasts(monkeypatch)
    fetched = _recording_fetch(monkeypatch)
    s = Settings()
    s.scheduler_refresh_market_data = True
    s.nse_rate_limit_delay_seconds = 2.0
    sched = AlertSchedulerService(settings=s, nse_client=AsyncMock(), clock=_at(IN_HOURS))

    summary = await sched.run_one_tick()

    assert len(fetched) == 100  # all swept, never silently capped
    warning = summary["sweep"]["warning"]
    assert "100 symbols" in warning and "200 s" in warning and "180 s" in warning
    assert sched.sweep_status["warning"] == warning
    assert any("can't finish" in r.getMessage() for r in caplog.records)


async def test_trade_events_are_broadcast_as_market_trade(db, monkeypatch):
    import json
    from pathlib import Path

    fixtures = Path(__file__).parent / "fixtures" / "nse"
    db.add(Stock(symbol="IRFC", quantity=1000))
    db.commit()
    _use_test_session(db, monkeypatch)
    broadcasts = _capture_broadcasts(monkeypatch)
    client = AsyncMock()
    client.fetch_slb_quote.return_value = json.loads((fixtures / "getquote_slbm_IRFC_t0_synthetic.json").read_text())
    now = {"t": IN_HOURS}
    sched = AlertSchedulerService(settings=_refresh_settings(0), nse_client=client, clock=lambda: now["t"])

    await sched.run_one_tick()
    client.fetch_slb_quote.return_value = json.loads((fixtures / "getquote_slbm_IRFC_t1_synthetic.json").read_text())
    now["t"] = IN_HOURS + datetime.timedelta(minutes=3)
    summary = await sched.run_one_tick()

    trades = [m for m in broadcasts if m["type"] == "market.trade"]
    assert len(trades) == 1
    t = trades[0]
    assert (t["symbol"], t["series_code"], t["quantity"], t["last_fee"]) == ("IRFC", "XN", 500, 0.33)
    assert t["inferred"] is True
    assert summary["trade_events"] == 1


# --- Phase 4.2: each tick auto-closes settled contracts (P&L once) -----------


async def test_tick_auto_closes_settled_contracts_once(db, monkeypatch):
    from slbm_tracker.db.models import PnLRecord

    stock = Stock(symbol="SBIN", quantity=500)
    db.add(stock)
    db.flush()
    db.add(LendingContract(stock_id=stock.id, expiry_date=datetime.date(2026, 10, 1), quantity_lent=100,
                           lending_fee=1.0, status="ACTIVE", trade_date=datetime.date(2026, 9, 1)))
    db.commit()
    _use_test_session(db, monkeypatch)
    _capture_broadcasts(monkeypatch)
    monkeypatch.setattr(scheduler_service, "today_ist", lambda: datetime.date(2026, 10, 5))
    sched = AlertSchedulerService(settings=_no_refresh_settings(), nse_client=AsyncMock(), clock=_at(IN_HOURS))

    summary = await sched.run_one_tick()
    await sched.run_one_tick()

    assert summary["auto_closed"] == 1
    assert db.query(LendingContract).one().status == "CLOSED"
    assert db.query(PnLRecord).count() == 1


# --- Phase 7.3: log how long the scheduler was paused (Mac asleep) ----------


async def test_a_long_gap_between_ticks_is_logged_as_sleep(db, monkeypatch, caplog):
    import logging

    _use_test_session(db, monkeypatch)
    _capture_broadcasts(monkeypatch)
    wall = {"t": 1_000_000.0}
    sched = AlertSchedulerService(settings=_no_refresh_settings(), nse_client=AsyncMock(),
                                  clock=_at(IN_HOURS), wall_clock=lambda: wall["t"])
    caplog.set_level(logging.INFO)

    first = await sched.run_one_tick()
    assert first["paused_seconds"] is None
    wall["t"] += 200  # a normal 180 s interval plus a little work
    assert (await sched.run_one_tick())["paused_seconds"] is None
    wall["t"] += 2 * 3600 + 30  # the Mac slept for two hours
    summary = await sched.run_one_tick()

    assert summary["paused_seconds"] == 2 * 3600 + 30
    assert any("paused for 120 min" in r.getMessage() for r in caplog.records)
