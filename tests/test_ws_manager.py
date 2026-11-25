"""Unit tests for the in-process WebSocket ConnectionManager (Phase 4).

All offline: fake WebSocket objects (``AsyncMock``), no real socket or port.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from slbm_tracker.api.ws_manager import ConnectionManager


def _fake_ws() -> AsyncMock:
    """A stand-in WebSocket exposing awaitable accept()/send_json()."""
    ws = AsyncMock()
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock()
    return ws


async def test_connect_accepts_and_registers():
    mgr = ConnectionManager()
    ws = _fake_ws()
    await mgr.connect(ws)
    ws.accept.assert_awaited_once()
    assert mgr.active_count == 1


def test_disconnect_is_idempotent():
    mgr = ConnectionManager()
    ws = _fake_ws()
    mgr.disconnect(ws)  # never connected -> discard, no error
    mgr.disconnect(ws)  # again -> still no error
    assert mgr.active_count == 0


async def test_broadcast_reaches_every_client():
    mgr = ConnectionManager()
    a, b = _fake_ws(), _fake_ws()
    await mgr.connect(a)
    await mgr.connect(b)
    delivered = await mgr.broadcast({"type": "ping"})
    assert delivered == 2
    a.send_json.assert_awaited_once_with({"type": "ping"})
    b.send_json.assert_awaited_once_with({"type": "ping"})


async def test_broadcast_drops_dead_peer_and_keeps_going():
    mgr = ConnectionManager()
    good, dead = _fake_ws(), _fake_ws()
    dead.send_json.side_effect = RuntimeError("socket closed")
    await mgr.connect(good)
    await mgr.connect(dead)
    delivered = await mgr.broadcast({"type": "x"})
    # The live peer still received it; the dead one was dropped, not fatal.
    assert delivered == 1
    good.send_json.assert_awaited_once()
    assert mgr.active_count == 1
    assert good in mgr._active and dead not in mgr._active


async def test_broadcast_with_no_clients_returns_zero():
    mgr = ConnectionManager()
    assert await mgr.broadcast({"type": "x"}) == 0


def test_submit_without_bound_loop_is_noop():
    mgr = ConnectionManager()
    # No loop bound (a pure unit test) -> logged and skipped, never raises.
    mgr.submit({"type": "x"})
    assert mgr.active_count == 0


async def test_submit_with_bound_loop_schedules_broadcast():
    mgr = ConnectionManager()
    ws = _fake_ws()
    await mgr.connect(ws)
    mgr.bind_loop(asyncio.get_running_loop())
    mgr.submit({"type": "hello"})
    # submit() is fire-and-forget via run_coroutine_threadsafe; yield control so
    # the scheduled broadcast coroutine runs on this loop.
    await asyncio.sleep(0.05)
    ws.send_json.assert_awaited_once_with({"type": "hello"})
