import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useWebSocket } from "@/hooks/useWebSocket";
import { FakeWebSocket } from "./fakeWebSocket";

let OriginalWebSocket: typeof WebSocket;

beforeEach(() => {
  OriginalWebSocket = globalThis.WebSocket;
  FakeWebSocket.reset();
  (globalThis as unknown as { WebSocket: unknown }).WebSocket = FakeWebSocket;
});

afterEach(() => {
  (globalThis as unknown as { WebSocket: unknown }).WebSocket = OriginalWebSocket;
  vi.useRealTimers();
});

describe("useWebSocket", () => {
  it("connects to the same-origin /api/ws URL and reports open", () => {
    const { result } = renderHook(() => useWebSocket("/api/ws"));

    expect(result.current.status).toBe("connecting");
    expect(FakeWebSocket.instances).toHaveLength(1);
    const socket = FakeWebSocket.instances[0];
    // URL is derived from window.location (host), not hard-coded.
    expect(socket.url).toMatch(/^wss?:\/\/[^/]+\/api\/ws$/);

    act(() => socket._open());
    expect(result.current.status).toBe("open");
  });

  it("dispatches JSON-parsed frames to onMessage", () => {
    const onMessage = vi.fn();
    renderHook(() => useWebSocket("/api/ws", { onMessage }));
    const socket = FakeWebSocket.instances[0];

    act(() => socket._open());
    act(() => socket._message({ type: "alert_fired", alert_id: 7, message: "hi" }));

    expect(onMessage).toHaveBeenCalledWith({ type: "alert_fired", alert_id: 7, message: "hi" });
  });

  it("ignores non-JSON frames without throwing", () => {
    const onMessage = vi.fn();
    renderHook(() => useWebSocket("/api/ws", { onMessage }));
    const socket = FakeWebSocket.instances[0];

    act(() => socket._open());
    act(() => socket.onmessage?.({ data: "not json{" }));

    expect(onMessage).not.toHaveBeenCalled();
  });

  it("reconnects after an unexpected close (capped backoff)", () => {
    vi.useFakeTimers();
    renderHook(() => useWebSocket("/api/ws"));
    const first = FakeWebSocket.instances[0];

    act(() => first._open());
    act(() => first._serverClose());
    // A reconnect is scheduled with the first backoff delay (~1s).
    act(() => {
      vi.advanceTimersByTime(1000);
    });

    expect(FakeWebSocket.instances).toHaveLength(2);
  });

  it("does not reconnect after unmount", () => {
    vi.useFakeTimers();
    const { unmount } = renderHook(() => useWebSocket("/api/ws"));
    const socket = FakeWebSocket.instances[0];
    act(() => socket._open());

    unmount();
    act(() => {
      vi.advanceTimersByTime(30000);
    });

    // Teardown detaches handlers and stops the backoff — no new sockets.
    expect(FakeWebSocket.instances).toHaveLength(1);
  });
});
