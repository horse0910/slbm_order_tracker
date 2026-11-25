import { useEffect, useRef, useState } from "react";

/**
 * Generic native-WebSocket hook (Phase 4 real-time push).
 *
 * - Builds the URL from `window.location` so it works behind the Vite dev proxy
 *   and in production: `ws(s)://<host><path>` — same-origin, never hard-coded.
 * - Auto-reconnects with capped exponential backoff (~1s → 15s), resetting the
 *   backoff on a successful open.
 * - JSON-parses each frame and hands the parsed value to `onMessage`; non-JSON
 *   frames are ignored rather than thrown.
 * - Cleans up the socket + pending reconnect timer on unmount and never
 *   reconnects after an intentional (component-driven) close.
 *
 * This is transport only. The consumer decides what an event means; per the
 * backend-owns-calculations rule, events should trigger a REST refetch, not a
 * client-side recompute.
 */

export type WebSocketStatus = "connecting" | "open" | "closed";

export interface UseWebSocketOptions {
  /** Invoked with the JSON-parsed payload of each received frame. */
  onMessage?: (data: unknown) => void;
  /** Gate the connection (e.g. disable in an environment without a socket). */
  enabled?: boolean;
}

export interface UseWebSocketState {
  status: WebSocketStatus;
}

const MIN_BACKOFF_MS = 1000;
const MAX_BACKOFF_MS = 15000;

function buildUrl(path: string): string {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}${path}`;
}

export function useWebSocket(
  path: string,
  { onMessage, enabled = true }: UseWebSocketOptions = {},
): UseWebSocketState {
  const [status, setStatus] = useState<WebSocketStatus>("closed");

  // Keep the latest callback without resubscribing the socket.
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  const socketRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptsRef = useRef(0);

  useEffect(() => {
    if (!enabled) {
      setStatus("closed");
      return;
    }
    // No global WebSocket (SSR / a test env without a stub): stay closed, never
    // throw. Real environments (browser, dev proxy) always provide one.
    if (typeof WebSocket === "undefined") {
      setStatus("closed");
      return;
    }

    let disposed = false;

    const clearReconnect = () => {
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
    };

    const scheduleReconnect = () => {
      if (disposed) return;
      const delay = Math.min(MAX_BACKOFF_MS, MIN_BACKOFF_MS * 2 ** attemptsRef.current);
      attemptsRef.current += 1;
      clearReconnect();
      reconnectTimerRef.current = setTimeout(connect, delay);
    };

    function connect() {
      if (disposed) return;
      setStatus("connecting");

      let socket: WebSocket;
      try {
        socket = new WebSocket(buildUrl(path));
      } catch {
        // Construction itself failed (bad URL / blocked) — back off and retry.
        scheduleReconnect();
        return;
      }
      socketRef.current = socket;

      socket.onopen = () => {
        if (disposed) return;
        attemptsRef.current = 0;
        setStatus("open");
      };

      socket.onmessage = (event: MessageEvent) => {
        if (disposed) return;
        let parsed: unknown;
        try {
          parsed = JSON.parse(typeof event.data === "string" ? event.data : "");
        } catch {
          return; // ignore non-JSON frames
        }
        onMessageRef.current?.(parsed);
      };

      // Let onclose drive reconnection — browsers fire close after an error.
      socket.onerror = () => {};

      socket.onclose = () => {
        socketRef.current = null;
        if (disposed) {
          setStatus("closed");
          return;
        }
        setStatus("connecting");
        scheduleReconnect();
      };
    }

    connect();

    return () => {
      // Intentional teardown: stop reconnecting and detach handlers so the
      // socket's own close event cannot schedule a new connection.
      disposed = true;
      clearReconnect();
      const socket = socketRef.current;
      socketRef.current = null;
      if (socket) {
        socket.onopen = null;
        socket.onmessage = null;
        socket.onerror = null;
        socket.onclose = null;
        try {
          socket.close();
        } catch {
          /* already closing/closed */
        }
      }
      setStatus("closed");
    };
  }, [path, enabled]);

  return { status };
}
