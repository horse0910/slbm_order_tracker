import { toDate } from "./format";

/**
 * Data-freshness heuristic (presentation only).
 *
 * The backend does not expose an explicit live/cached flag on SLB responses — it
 * silently falls back to the most recent cached snapshot when a live NSE fetch
 * fails. We therefore infer freshness from the snapshot `timestamp`: a quote
 * whose timestamp is older than `staleAfterMs` is flagged as possibly stale so
 * the UI can warn the user. This is a UI concern, not a re-implementation of any
 * backend calculation.
 */

export const DEFAULT_STALE_AFTER_MS = 2 * 60 * 1000; // 2 minutes

export interface Freshness {
  isStale: boolean;
  ageMs: number | null;
  timestamp: string | null;
}

export function assessFreshness(
  timestamp: string | null | undefined,
  staleAfterMs: number = DEFAULT_STALE_AFTER_MS,
  now: number = Date.now(),
): Freshness {
  const d = toDate(timestamp);
  if (!d) return { isStale: false, ageMs: null, timestamp: timestamp ?? null };
  const ageMs = now - d.getTime();
  return { isStale: ageMs > staleAfterMs, ageMs, timestamp: timestamp ?? null };
}

/** Latest (max) timestamp across a set of quotes, for a page-level freshness badge. */
export function latestTimestamp(timestamps: Array<string | null | undefined>): string | null {
  let best: { ms: number; raw: string } | null = null;
  for (const ts of timestamps) {
    const d = toDate(ts);
    if (!d || !ts) continue;
    const ms = d.getTime();
    if (!best || ms > best.ms) best = { ms, raw: ts };
  }
  return best?.raw ?? null;
}
