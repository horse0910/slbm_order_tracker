import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useWebSocket, type WebSocketStatus } from "@/hooks/useWebSocket";
import { Toaster, type ToastItem } from "@/components/Toaster";
import type { AlertFiredEvent, LiveAlertEvent } from "@/types";

/**
 * App-wide real-time alerts provider (Phase 4).
 *
 * Owns the single WebSocket for the whole app (mounted once in App.tsx). It
 * surfaces connection `status`, the `lastEvent`, and a monotonic `eventCount`
 * that pages watch to trigger a REST refetch — never a client-side recompute
 * (the backend owns all calculations). On an `alert_fired` frame it also raises
 * a lightweight in-app toast.
 *
 * `useLiveAlerts()` returns a safe inert default when used outside the provider,
 * so pages (and their unit tests) render fine without it mounted.
 */

const ALERT_TYPE_LABELS: Record<string, string> = {
  YIELD_THRESHOLD: "Gross yield threshold",
  LENDING_OPPORTUNITY: "Lending opportunity",
  ABOVE_AVERAGE: "Above average",
  TIGHT_SPREAD: "Tight spread",
  ROLLOVER: "Rollover",
  EXPIRY_REMINDER: "Expiry reminder",
};

function alertTitle(alertType: string): string {
  return `${ALERT_TYPE_LABELS[alertType] ?? alertType} alert`;
}

export interface LiveAlertsContextValue {
  status: WebSocketStatus;
  /** Increments once per received `alert_fired` frame. */
  eventCount: number;
  lastEvent: AlertFiredEvent | null;
}

const DEFAULT_VALUE: LiveAlertsContextValue = {
  status: "closed",
  eventCount: 0,
  lastEvent: null,
};

const LiveAlertsContext = createContext<LiveAlertsContextValue>(DEFAULT_VALUE);

export function useLiveAlerts(): LiveAlertsContextValue {
  return useContext(LiveAlertsContext);
}

export function LiveAlertsProvider({ children }: { children: ReactNode }) {
  const [eventCount, setEventCount] = useState(0);
  const [lastEvent, setLastEvent] = useState<AlertFiredEvent | null>(null);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const toastIdRef = useRef(0);

  const dismissToast = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const handleMessage = useCallback((data: unknown) => {
    if (!data || typeof data !== "object" || !("type" in data)) return;
    const event = data as LiveAlertEvent;
    if (event.type !== "alert_fired") return; // connected / scheduler_tick: pill only

    setLastEvent(event);
    setEventCount((n) => n + 1);

    const id = ++toastIdRef.current;
    setToasts((prev) => [
      ...prev,
      { id, title: alertTitle(event.alert_type), body: event.message ?? "Condition met." },
    ]);
  }, []);

  const { status } = useWebSocket("/api/ws", { onMessage: handleMessage });

  const value = useMemo<LiveAlertsContextValue>(
    () => ({ status, eventCount, lastEvent }),
    [status, eventCount, lastEvent],
  );

  return (
    <LiveAlertsContext.Provider value={value}>
      {children}
      <Toaster toasts={toasts} onDismiss={dismissToast} />
    </LiveAlertsContext.Provider>
  );
}
