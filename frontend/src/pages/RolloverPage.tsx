import { rolloverApi } from "@/api/rollover";
import { useApiResource } from "@/hooks/useApiResource";
import { Card, SectionHeader } from "@/components/Card";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/StateViews";
import { DaysBadge } from "@/components/StatusPill";
import { formatDate, formatDateTime, formatPercent, formatPrice, formatQty } from "@/utils/format";
import type { RolloverRecommendation } from "@/types";

/** Factual "favorable vs current" tag — not advice, just the fee comparison. */
function FavorabilityTag({ favorable }: { favorable: boolean | null }) {
  if (favorable === null) {
    return <span className="pill bg-surface-3 text-slate-400" title="Not comparable">— n/a</span>;
  }
  return favorable ? (
    <span className="pill bg-pos/15 text-pos" title="Best available future fee ≥ current fee">
      ▲ ≥ current fee
    </span>
  ) : (
    <span className="pill bg-warn/20 text-warn" title="Best available future fee is below current fee">
      ▼ below current fee
    </span>
  );
}

function RecommendationCard({ rec }: { rec: RolloverRecommendation }) {
  const insufficient = rec.status === "INSUFFICIENT_DATA";
  return (
    <Card className="panel-pad">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-slate-100">{rec.symbol}</h3>
            <span className="text-2xs text-slate-500">
              {rec.current_series ?? "—"} · expires {formatDate(rec.current_expiry)}
            </span>
          </div>
          <p className="mt-0.5 text-2xs text-slate-500">
            Current fee ₹{formatPrice(rec.current_fee, 3)}/share
          </p>
        </div>
        <div className="flex items-center gap-2">
          {rec.days_remaining >= 0 ? (
            <DaysBadge days={rec.days_remaining} />
          ) : (
            <span className="pill bg-neg/20 text-neg">Expired {Math.abs(rec.days_remaining)}d ago</span>
          )}
        </div>
      </div>

      <p className="mt-3 text-xs text-slate-300">{rec.rationale}</p>

      {insufficient ? (
        <div className="mt-3 rounded-md border border-line bg-surface-2/50 px-3 py-2 text-2xs text-slate-400">
          No future-series market data has been captured for {rec.symbol}. Open the Live SLB Data
          page for this symbol to fetch it, then revisit this advisor.
        </div>
      ) : (
        <div className="mt-3 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <FavorabilityTag favorable={rec.favorable} />
            {rec.fee_delta !== null ? (
              <span className="text-2xs text-slate-400">
                Fee delta {rec.fee_delta >= 0 ? "+" : ""}
                ₹{formatPrice(rec.fee_delta, 3)}/share vs best available future series
              </span>
            ) : null}
          </div>

          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr className="border-b border-line">
                  <th className="th">Future series</th>
                  <th className="th">Expiry</th>
                  <th className="th text-right">Market fee/share</th>
                  <th className="th text-right">Yield p.a.</th>
                  <th className="th text-right">Open pos.</th>
                  <th className="th">Data as of</th>
                </tr>
              </thead>
              <tbody>
                {rec.options.map((o, i) => {
                  const isBest = i === 0;
                  return (
                    <tr
                      key={`${o.series}-${o.expiry_date}`}
                      className={`border-b border-line/50 ${isBest ? "bg-surface-2/40" : ""}`}
                    >
                      <td className="td font-medium">
                        {o.series ?? "—"}
                        {isBest ? (
                          <span className="ml-1.5 pill bg-accent-soft text-white">Best</span>
                        ) : null}
                      </td>
                      <td className="td text-slate-300">{formatDate(o.expiry_date)}</td>
                      <td className="td text-right tnum">
                        {o.market_fee !== null ? `₹${formatPrice(o.market_fee, 3)}` : "—"}
                      </td>
                      <td className="td text-right tnum">
                        {o.annualized_yield !== null ? formatPercent(o.annualized_yield) : "—"}
                      </td>
                      <td className="td text-right tnum">
                        {o.open_positions !== null ? formatQty(o.open_positions) : "—"}
                      </td>
                      <td className="td text-2xs text-slate-500">{formatDateTime(o.data_as_of)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Card>
  );
}

export function RolloverPage() {
  const { data, error, loading, refreshing, refetch } = useApiResource<RolloverRecommendation[]>(
    (signal) => rolloverApi.list(signal),
    [],
  );

  const recs = data ?? [];

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-slate-500">
          Decision-support for contracts nearing expiry. Comparisons use captured market snapshots
          only — this advisor does not place orders or predict future prices.
        </p>
        <button type="button" className="btn-ghost" onClick={refetch} disabled={refreshing}>
          Refresh
        </button>
      </div>

      {loading ? (
        <Card>
          <SectionHeader title="Loading rollover advisor…" />
          <TableSkeleton rows={3} cols={4} />
        </Card>
      ) : error ? (
        <Card className="panel-pad">
          <ErrorState error={error} onRetry={refetch} />
        </Card>
      ) : recs.length === 0 ? (
        <Card>
          <EmptyState
            icon="⟳"
            title="No contracts need rollover attention"
            message="Contracts nearing their expiry window will appear here with a data-backed comparison of available future series. Open contracts on the Contracts page to start tracking."
          />
        </Card>
      ) : (
        <div className="space-y-4">
          {recs.map((rec) => (
            <RecommendationCard key={rec.contract_id} rec={rec} />
          ))}
        </div>
      )}
    </div>
  );
}
