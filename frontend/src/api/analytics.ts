import { http } from "./client";
import type {
  AnalyticsSummary,
  EquityHistory,
  EquityRefreshResult,
  SLBHistory,
} from "@/types";

/**
 * Analytics API (read-only roll-ups + historical trends). All aggregation and
 * money math is backend-owned; the frontend only renders the returned figures.
 *
 * Only `refreshEquityHistory` reaches NSE (server-side) — every read endpoint
 * serves persisted data and never triggers a live fetch.
 */
export const analyticsApi = {
  summary: (signal?: AbortSignal) =>
    http.get<AnalyticsSummary>("/api/analytics/summary", signal),

  /**
   * Daily SLB yield/spread/open-positions trend. Pass a `symbol` for the
   * per-symbol trend, or null for the owned-portfolio aggregate. `days` is
   * clamped server-side to the snapshot-retention horizon.
   */
  history: (symbol: string | null, days: number, signal?: AbortSignal) => {
    const params = new URLSearchParams({ days: String(days) });
    if (symbol) params.set("symbol", symbol);
    return http.get<SLBHistory>(`/api/analytics/history?${params.toString()}`, signal);
  },

  /**
   * Persisted equity price/volume bars + descriptive stats for one owned
   * symbol. Read-only: does NOT hit NSE (use refreshEquityHistory to fetch).
   */
  equityHistory: (symbol: string, days: number, signal?: AbortSignal) => {
    const params = new URLSearchParams({ symbol, days: String(days) });
    return http.get<EquityHistory>(
      `/api/analytics/equity-history?${params.toString()}`,
      signal,
    );
  },

  /**
   * Fetch equity price/volume history from NSE and UPSERT it locally. Pass a
   * `symbol` to refresh one holding, or omit it to refresh all owned holdings.
   * The only analytics call that reaches NSE; a fetch failure surfaces as 502.
   */
  refreshEquityHistory: (symbol?: string | null, days?: number) => {
    const params = new URLSearchParams();
    if (symbol) params.set("symbol", symbol);
    if (days) params.set("days", String(days));
    const qs = params.toString();
    return http.post<EquityRefreshResult>(
      `/api/analytics/equity-history/refresh${qs ? `?${qs}` : ""}`,
    );
  },
};
