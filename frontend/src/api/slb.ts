import { http } from "./client";
import type { NSEHistory, SLBLiveResponse, SLBQuote } from "@/types";

/**
 * SLB market-data API.
 *
 * M-2 decision: the Live SLB view is a per-symbol page. `GET /api/slb/live/{symbol}`
 * already returns every active contract (series + expiry) for that symbol, so it
 * is the authoritative source for BOTH the market snapshot and the available
 * expiry tabs — the tabs are derived from `response.contracts`.
 *
 * The other Phase 1 endpoints are intentionally NOT called here:
 *  - `/api/slb/series-master` returns the GLOBAL series dropdown (all months),
 *    which is not per-symbol; using it could surface expiries with no contract
 *    for the selected symbol (empty tabs).
 *  - `/api/slb/eligible` is a LIVE NSE fetch of exchange-eligible securities;
 *    the symbol picker is instead sourced from the local tracked-symbol
 *    universe (`/api/symbols`, unioned with owned holdings), which is stable
 *    offline and needs no live NSE round-trip. The two are intentionally
 *    distinct concepts and `/eligible` is left unchanged.
 *  - `/api/slb/series/{series}` is a cross-symbol (by-month) view, out of scope
 *    for the per-symbol Phase 2 page.
 *
 * These are left to the phases that actually need them (e.g. an all-market
 * series browser) rather than adding unused calls now.
 */
export const slbApi = {
  liveBySymbol: (symbol: string, signal?: AbortSignal) =>
    http.get<SLBLiveResponse>(`/api/slb/live/${encodeURIComponent(symbol)}`, signal),

  /** Phase 6.3: every series with code, family, depth and NSE settlement; never a 500. */
  quote: (symbol: string, signal?: AbortSignal) =>
    http.get<SLBQuote>(`/api/slb/quote/${encodeURIComponent(symbol)}`, signal),

  /** NSE daily SLB trades between two ISO dates (NSE keeps about two months). */
  history: (symbol: string, from: string, to: string, signal?: AbortSignal) =>
    http.get<NSEHistory>(
      `/api/slb/quote/${encodeURIComponent(symbol)}/history?from=${from}&to=${to}`,
      signal,
    ),
};
