import { http } from "./client";
import type { RolloverRecommendation } from "@/types";

/**
 * Rollover Advisor API (read-only decision-support).
 *
 * `GET /api/rollover` returns one recommendation per rollover-candidate
 * contract (nearing expiry), most urgent first. All figures are backend-derived
 * from persisted market snapshots — the endpoint never executes orders and
 * never fabricates future-series data.
 */
export const rolloverApi = {
  list: (signal?: AbortSignal) =>
    http.get<RolloverRecommendation[]>("/api/rollover", signal),
};
