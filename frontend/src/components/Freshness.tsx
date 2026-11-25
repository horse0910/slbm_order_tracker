import { formatRelative } from "@/utils/format";
import { assessFreshness } from "@/utils/freshness";

interface FreshnessIndicatorProps {
  /** Timestamp of the underlying data (snapshot time). */
  timestamp?: string | null;
  /** Wall-clock time of the last successful fetch (ms). */
  lastUpdated?: number | null;
  refreshing?: boolean;
}

/**
 * Shows when data was last fetched and whether the underlying snapshot may be
 * stale/cached (inferred from its timestamp — the backend has no explicit
 * live/cached flag).
 */
export function FreshnessIndicator({
  timestamp,
  lastUpdated,
  refreshing,
}: FreshnessIndicatorProps) {
  const freshness = assessFreshness(timestamp);

  return (
    <div className="flex items-center gap-2 text-2xs text-slate-500">
      {refreshing ? (
        <span className="h-2.5 w-2.5 animate-spin rounded-full border-2 border-slate-600 border-t-accent" />
      ) : (
        <span
          className={`h-2 w-2 rounded-full ${freshness.isStale ? "bg-warn" : "bg-pos"}`}
          aria-hidden
        />
      )}
      {lastUpdated ? (
        <span className="tnum">Updated {formatRelative(new Date(lastUpdated).toISOString())}</span>
      ) : (
        <span>Awaiting data</span>
      )}
      {freshness.isStale ? <StaleBadge timestamp={freshness.timestamp} /> : null}
    </div>
  );
}

export function StaleBadge({ timestamp }: { timestamp?: string | null }) {
  return (
    <span
      className="pill bg-warn/20 text-warn"
      title={
        timestamp
          ? `Latest snapshot is from ${formatRelative(timestamp)} — data may be cached.`
          : "Data may be cached."
      }
    >
      ⚠ Possibly stale
    </span>
  );
}
