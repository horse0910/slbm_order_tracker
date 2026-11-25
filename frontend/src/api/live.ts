import { http } from "./client";
import type { LiveBoard, TradeFeed } from "@/types";

/**
 * Live board API (Phase 2.13). The backend sweeps NSE on its own schedule and
 * these routes only read what it stored; the page refetches when the
 * WebSocket says something changed.
 */
export const liveApi = {
  board: (signal?: AbortSignal) => http.get<LiveBoard>("/api/portfolio/live", signal),
  trades: (signal?: AbortSignal) => http.get<TradeFeed>("/api/portfolio/live/trades", signal),
};
