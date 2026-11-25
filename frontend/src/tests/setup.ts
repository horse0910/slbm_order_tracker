import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  localStorage.clear();
});

// Recharts' ResponsiveContainer relies on ResizeObserver, absent in jsdom.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
if (!("ResizeObserver" in globalThis)) {
  (globalThis as unknown as { ResizeObserver: typeof ResizeObserverStub }).ResizeObserver =
    ResizeObserverStub;
}

if (!window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList;
}

// jsdom has no WebSocket, and Node's global one would attempt a real network
// connection. Install an inert stub so mounting LiveAlertsProvider in component
// tests never opens a socket. Tests that exercise the socket (useWebSocket,
// AlertsPage live event) install their own controllable fake and restore this.
class WebSocketStub {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  readyState = WebSocketStub.CONNECTING;
  onopen: unknown = null;
  onmessage: unknown = null;
  onerror: unknown = null;
  onclose: unknown = null;
  constructor(public url?: string) {}
  send() {}
  close() {}
  addEventListener() {}
  removeEventListener() {}
}
(globalThis as unknown as { WebSocket: typeof WebSocketStub }).WebSocket = WebSocketStub;
