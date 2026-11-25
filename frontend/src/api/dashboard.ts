import { http } from "./client";
import type { DashboardSummary, HealthResponse } from "@/types";

export const dashboardApi = {
  summary: (signal?: AbortSignal) => http.get<DashboardSummary>("/api/dashboard/summary", signal),
};

export const healthApi = {
  check: (signal?: AbortSignal) => http.get<HealthResponse>("/api/health", signal),
};
