"""In-process WebSocket connection manager for real-time push (Phase 4).

A single module-level :data:`ws_manager` fans out backend-computed JSON messages
to every connected client. It is deliberately tiny and dependency-free:

* The whole app runs on one asyncio event loop (uvicorn / Starlette
  ``TestClient``), so the async scheduler and the WS handlers share it and can
  ``await broadcast()`` directly.
* Synchronous callers — the sync ``POST /api/alerts/evaluate`` handler, which
  FastAPI runs in a threadpool — cannot ``await``; they use the thread-safe
  :meth:`ConnectionManager.submit`, which hands the coroutine to the bound loop
  via :func:`asyncio.run_coroutine_threadsafe` and returns immediately
  (fire-and-forget).

No authentication and no per-client routing: this is a localhost single-user
desktop app; every client sees every backend-pushed event. The endpoint is
same-origin under ``/api`` — never bind the server to ``0.0.0.0``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from starlette.websockets import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Tracks live WebSocket clients and broadcasts JSON messages to them."""

    def __init__(self) -> None:
        self._active: set[WebSocket] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # -- loop binding (startup) ---------------------------------------------
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Record the running event loop so synchronous callers can reach it.

        Called once from the FastAPI ``lifespan`` on startup — always, even when
        the scheduler is disabled, so :meth:`submit` still works for the manual
        evaluate route. Rebinding on a fresh lifespan (e.g. a new ``TestClient``)
        is intentional: the latest running loop wins.
        """
        self._loop = loop

    # -- connection lifecycle -----------------------------------------------
    async def connect(self, websocket: WebSocket) -> None:
        """Accept the handshake and register the client."""
        await websocket.accept()
        self._active.add(websocket)
        logger.info("WebSocket client connected (%d active).", len(self._active))

    def disconnect(self, websocket: WebSocket) -> None:
        """Deregister a client. Idempotent — safe to call more than once."""
        self._active.discard(websocket)
        logger.info("WebSocket client disconnected (%d active).", len(self._active))

    @property
    def active_count(self) -> int:
        """Number of currently-registered clients (handy for tests/diagnostics)."""
        return len(self._active)

    # -- broadcast (async, runs on the loop) --------------------------------
    async def broadcast(self, message: dict[str, Any]) -> int:
        """Send ``message`` as JSON to every connected client.

        Iterates a *snapshot* of the client set so a disconnect mid-send never
        mutates what we iterate. Any socket that raises is treated as a dead peer
        and dropped; one dead peer never blocks delivery to the rest. Returns the
        number of clients the message reached.
        """
        if not self._active:
            return 0
        delivered = 0
        for ws in list(self._active):
            try:
                await ws.send_json(message)
                delivered += 1
            except Exception as exc:  # noqa: BLE001 - dead peer: drop and continue
                logger.debug("Dropping dead WebSocket peer: %s", exc)
                self.disconnect(ws)
        return delivered

    # -- submit (thread-safe, for synchronous / off-loop callers) -----------
    def submit(self, message: dict[str, Any]) -> None:
        """Schedule a broadcast from a synchronous / off-loop caller.

        Fire-and-forget and thread-safe: hands the coroutine to the bound loop
        and returns immediately. It never raises into the caller — a push failure
        must never break the request that triggered it. If no loop is bound yet
        (a unit test that never started the app), the message is logged and
        skipped.
        """
        loop = self._loop
        if loop is None:
            logger.debug("submit() called with no bound event loop; skipping push.")
            return
        try:
            future = asyncio.run_coroutine_threadsafe(self.broadcast(message), loop)
        except RuntimeError as exc:
            # Loop closed / not running (e.g. mid-shutdown). Never propagate.
            logger.debug("submit() could not schedule broadcast: %s", exc)
            return

        def _log_result(fut) -> None:
            try:
                fut.result()
            except Exception as exc:  # noqa: BLE001 - log only, never raise
                logger.debug("Async broadcast via submit() failed: %s", exc)

        future.add_done_callback(_log_result)


# Module-level singleton imported by the WS route, the scheduler, and the sync
# evaluate route. Pure asyncio, one event loop, no locks.
ws_manager = ConnectionManager()
