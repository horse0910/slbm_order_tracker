import { http } from "./client";
import type { Alert, AlertCreate, AlertDeliveryLog, AlertEvaluationRun } from "@/types";

/**
 * Alerts API (rules + evaluation).
 *
 * Rules are configured here; evaluation and delivery are backend-owned. The
 * frontend never evaluates conditions itself and never handles notification
 * credentials. `evaluate` runs the deterministic engine server-side and returns
 * what fired plus best-effort delivery outcomes. `deliveries` reads the
 * persisted delivery-audit log (outcomes only, never credentials).
 */
export const alertsApi = {
  list: (activeOnly = false, signal?: AbortSignal) =>
    http.get<Alert[]>(`/api/alerts${activeOnly ? "?active_only=true" : ""}`, signal),

  create: (payload: AlertCreate) => http.post<Alert>("/api/alerts", payload),

  setActive: (id: number, isActive: boolean) =>
    http.patch<Alert>(`/api/alerts/${id}`, { is_active: isActive }),

  remove: (id: number) => http.del<void>(`/api/alerts/${id}`),

  evaluate: () => http.post<AlertEvaluationRun>("/api/alerts/evaluate"),

  deliveries: (signal?: AbortSignal) =>
    http.get<AlertDeliveryLog>("/api/alerts/deliveries", signal),
};
