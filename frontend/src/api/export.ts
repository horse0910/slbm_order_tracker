import { apiUrl } from "./client";

/**
 * CSV export downloads (Phase 5C).
 *
 * These are plain browser downloads, NOT JSON fetches: each backend endpoint
 * returns `text/csv` with a `Content-Disposition: attachment` header, so the
 * server owns the filename and the browser saves the file. This module only
 * builds the URL (honoring `VITE_API_BASE_URL`, the same base as the fetch
 * client) and opens it via a transient anchor — there is no client-side money
 * math or recompute; every figure is serialized by the owning backend service.
 *
 * PDF is intentionally NOT here: the "report" PDF is produced by the browser's
 * own print dialog ("Print / Save as PDF") over a `@media print` stylesheet,
 * never a server-side or bundled PDF library.
 */
export const exportApi = {
  /** Persisted P&L records, one row each. */
  pnlCsvUrl: () => apiUrl("/api/export/pnl.csv"),

  /** Derived contract views (lifecycle, days-remaining, fee income). */
  contractsCsvUrl: () => apiUrl("/api/export/contracts.csv"),

  /** Two-section analytics report: FY income + per-symbol yield. */
  analyticsCsvUrl: () => apiUrl("/api/export/analytics.csv"),

  /**
   * SLB yield-trend CSV for one `symbol`, or the owned-portfolio aggregate when
   * `symbol` is null. `days` mirrors the on-screen window and is clamped
   * server-side to the snapshot-retention horizon.
   */
  historyCsvUrl: (symbol: string | null, days: number) => {
    const params = new URLSearchParams({ days: String(days) });
    if (symbol) params.set("symbol", symbol);
    return apiUrl(`/api/export/history.csv?${params.toString()}`);
  },

  /**
   * Open a CSV download for `url` via a transient, self-removing anchor. The
   * `Content-Disposition` header (not this call) sets the saved filename.
   */
  download: (url: string) => triggerDownload(url),
};

function triggerDownload(url: string): void {
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.rel = "noopener";
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
}
