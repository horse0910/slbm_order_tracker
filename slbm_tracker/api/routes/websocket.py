"""Real-time push WebSocket endpoint (Phase 4).

Registered under the ``/api`` prefix in :mod:`slbm_tracker.api.app`, so the
resolved path is ``/api/ws`` (same-origin behind the Vite dev proxy and in
production). The endpoint carries only backend-computed, read-only event frames
(``connected``, ``alert_fired``, ``scheduler_tick``); it accepts no commands from
the client and performs no calculation. The frontend treats an inbound frame as
a nudge to refetch authoritative REST data — it never recomputes money or status
from a frame (backend-only-calc rule).

Timestamps in frames are naive-UTC ISO strings (no trailing ``Z``), matching the
REST serialization convention the frontend formatter already assumes.
"""

from __future__ import annotations

import datetime
import logging

from fastapi import APIRouter
from starlette.websockets import WebSocket, WebSocketDisconnect

from slbm_tracker.api.ws_manager import ws_manager
from slbm_tracker.config import ALLOWED_ORIGINS

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws")
async def alerts_ws(websocket: WebSocket) -> None:
    """Accept a client and hold the socket open to push live events.

    No meaningful input is expected from the client; ``receive_text()`` is used
    only to await the close frame (and to notice an abrupt drop). All outbound
    traffic is server-initiated via :data:`ws_manager`.
    """
    # Cross-site WebSocket hijacking guard. Browsers do not apply same-origin
    # policy to WebSocket upgrades and CORSMiddleware does not cover them, so a
    # page on any site could otherwise open this socket and passively read every
    # pushed frame. Reject a handshake whose Origin is present but not on the
    # shared allowlist; a MISSING Origin (non-browser / same-process clients such
    # as the test harness) is allowed. Close BEFORE accept() to deny the upgrade.
    origin = websocket.headers.get("origin")
    if origin is not None and origin not in ALLOWED_ORIGINS:
        logger.warning("Rejected WebSocket handshake from disallowed origin: %s", origin)
        await websocket.close(code=1008)  # 1008 = policy violation
        return
    await ws_manager.connect(websocket)
    try:
        # Immediate server-side acknowledgement: confirms the server accepted
        # AND registered this client (a bare TCP open does not). The frontend
        # flips its "Live" indicator on this frame.
        await websocket.send_json(
            {"type": "connected", "at": datetime.datetime.utcnow().isoformat()}
        )
        while True:
            # Blocks until the client sends (ignored) or disconnects.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001 - never let a bad socket crash the app
        logger.debug("WebSocket receive loop ended: %s", exc)
    finally:
        ws_manager.disconnect(websocket)
