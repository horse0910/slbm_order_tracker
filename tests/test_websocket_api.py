"""Behavioral tests for the /api/ws real-time push endpoint (Phase 4).

Uses Starlette's ``TestClient.websocket_connect`` (runs the app in an in-process
portal — binds no port, touches no network). The autouse fixture in conftest
keeps the background scheduler off, so the only frames here are the ones these
tests drive.
"""

from __future__ import annotations

import pytest
from starlette.websockets import WebSocketDisconnect

from slbm_tracker.api.ws_manager import ws_manager


def test_ws_sends_connected_ack_on_connect(client):
    with client.websocket_connect("/api/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "connected"
        assert "at" in hello and hello["at"]
        # naive-UTC ISO, no trailing "Z" (matches REST serialization).
        assert not hello["at"].endswith("Z")


def test_ws_receives_a_broadcast_frame(client):
    with client.websocket_connect("/api/ws") as ws:
        # Draining the hello frame guarantees the socket is registered, so the
        # subsequent submit() broadcast is deterministic (no connect race).
        assert ws.receive_json()["type"] == "connected"

        # Mirror the sync evaluate route: push via the thread-safe entry. The
        # lifespan bound the loop on startup, so this reaches connected clients.
        ws_manager.submit({"type": "alert_fired", "alert_id": 42, "message": "hi"})

        frame = ws.receive_json()
        assert frame["type"] == "alert_fired"
        assert frame["alert_id"] == 42
        assert frame["message"] == "hi"


def test_ws_registers_client_on_connect(client):
    before = ws_manager.active_count
    with client.websocket_connect("/api/ws") as ws:
        # The hello frame is sent only AFTER connect() registers the socket, so
        # receiving it proves registration deterministically (no timing race).
        assert ws.receive_json()["type"] == "connected"
        assert ws_manager.active_count == before + 1


def test_ws_rejects_disallowed_origin(client):
    """Cross-site WebSocket hijacking guard: a browser Origin that is not on the
    allowlist is denied at the handshake (closed before accept), so a drive-by
    page cannot open the live feed."""
    before = ws_manager.active_count
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/api/ws", headers={"origin": "http://evil.example"}
        ) as ws:
            ws.receive_json()
    # The rejected handshake was never registered as a live client.
    assert ws_manager.active_count == before


def test_ws_allows_configured_origin(client):
    """An allowlisted browser Origin (e.g. the Vite dev server) connects normally."""
    with client.websocket_connect(
        "/api/ws", headers={"origin": "http://localhost:5180"}
    ) as ws:
        assert ws.receive_json()["type"] == "connected"
