/** Runtime-tunable frontend configuration (presentation / polling only). */

function readInt(value: string | undefined, fallback: number): number {
  const n = value ? Number.parseInt(value, 10) : NaN;
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

/** Default polling interval for live views (ms). Configurable via env. */
export const POLL_INTERVAL_MS = readInt(import.meta.env.VITE_POLL_INTERVAL_MS, 15_000);

/** Dashboard refreshes less aggressively than the live SLB view. */
export const DASHBOARD_POLL_INTERVAL_MS = 30_000;
