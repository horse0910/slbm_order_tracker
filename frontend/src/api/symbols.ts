import { http } from "./client";
import type { SymbolUniverse } from "@/types";

/**
 * Tracked SLB symbol-universe API.
 *
 * `GET /api/symbols` returns the configured/seeded universe of symbols the app
 * monitors for SLB. This is the authoritative source for symbol selectors — it
 * is independent of portfolio holdings and does not require a live NSE call
 * (unlike `/api/slb/eligible`, which is a live exchange-eligibility feed).
 */
export const symbolsApi = {
  list: (signal?: AbortSignal) => http.get<SymbolUniverse>("/api/symbols", signal),
};
