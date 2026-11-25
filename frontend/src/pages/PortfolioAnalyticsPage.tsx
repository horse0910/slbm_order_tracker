import { useCallback, useState } from "react";
import {
  Bar,
  BarChart,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { analyticsApi } from "@/api/analytics";
import { portfolioApi } from "@/api/portfolio";
import { exportApi } from "@/api/export";
import { toApiError } from "@/api/client";
import { useApiResource } from "@/hooks/useApiResource";
import { Card, SectionHeader } from "@/components/Card";
import { ExportCsvButton } from "@/components/ExportButton";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/StateViews";
import { PriceHistoryChart } from "@/components/PriceHistoryChart";
import { YieldHistoryChart } from "@/components/YieldHistoryChart";
import { formatINR, formatPercent, formatPrice, formatQty } from "@/utils/format";
import type {
  AnalyticsSummary,
  EquityHistory,
  EquityStats,
  SLBHistory,
  Stock,
  SymbolYield,
} from "@/types";

const TOOLTIP_STYLE = {
  background: "#111725",
  border: "1px solid #26304a",
  borderRadius: 8,
  fontSize: 12,
} as const;

const DAY_RANGE_OPTIONS = [30, 90, 180, 365] as const;

function YieldChart({ data }: { data: SymbolYield[] }) {
  const chartData = data
    .filter((s) => s.latest_yield !== null)
    .slice(0, 12)
    .map((s) => ({ name: s.symbol, yield: Number((s.latest_yield ?? 0).toFixed(2)) }));
  if (chartData.length === 0) return null;
  return (
    <div className="h-48 w-full" aria-hidden>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
          <XAxis dataKey="name" tick={{ fill: "#94a3b8", fontSize: 11 }} axisLine={{ stroke: "#26304a" }} tickLine={false} />
          <YAxis tick={{ fill: "#94a3b8", fontSize: 11 }} axisLine={false} tickLine={false} width={44} tickFormatter={(v) => `${v}%`} />
          <Tooltip
            cursor={{ fill: "#1e273b55" }}
            contentStyle={TOOLTIP_STYLE}
            labelStyle={{ color: "#e2e8f0" }}
            formatter={(value) => [formatPercent(Number(value)), "Latest yield"]}
          />
          <Bar dataKey="yield" radius={[3, 3, 0, 0]} maxBarSize={40}>
            {chartData.map((e) => (
              <Cell key={e.name} fill="#3fb98a" />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

const STATUS_LABELS: Record<string, string> = {
  ACTIVE: "Active",
  EXPIRING_SOON: "Expiring soon",
  EXPIRED: "Expired",
  RECALLED: "Recalled",
  CLOSED: "Closed",
  ROLLED_OVER: "Rolled over",
};

function StatChip({
  label,
  value,
  valueClass = "text-slate-100",
}: {
  label: string;
  value: string;
  valueClass?: string;
}) {
  return (
    <div className="rounded-md border border-line bg-surface-2/50 px-3 py-2">
      <p className="text-2xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className={`mt-1 text-sm font-semibold tnum ${valueClass}`}>{value}</p>
    </div>
  );
}

function EquityStatsGrid({ stats }: { stats: EquityStats }) {
  const ret = stats.period_return_pct;
  const retClass =
    ret === null || ret === 0 ? "text-slate-100" : ret > 0 ? "text-pos" : "text-neg";
  const retValue = ret === null ? "—" : `${ret > 0 ? "+" : ""}${formatPercent(ret)}`;
  const price = (v: number | null) => (v !== null ? `₹${formatPrice(v)}` : "—");
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
      <StatChip label="Samples" value={formatQty(stats.samples)} />
      <StatChip label="Period return" value={retValue} valueClass={retClass} />
      <StatChip label="First close" value={price(stats.first_close)} />
      <StatChip label="Last close" value={price(stats.last_close)} />
      <StatChip label="Period high" value={price(stats.period_high)} />
      <StatChip label="Period low" value={price(stats.period_low)} />
      <StatChip
        label="Avg volume"
        value={stats.avg_volume !== null ? formatQty(stats.avg_volume) : "—"}
      />
      <StatChip
        label="Daily volatility"
        value={
          stats.daily_return_volatility_pct !== null
            ? formatPercent(stats.daily_return_volatility_pct)
            : "—"
        }
      />
    </div>
  );
}

export function PortfolioAnalyticsPage() {
  const [selectedSymbol, setSelectedSymbol] = useState("");
  const [days, setDays] = useState<number>(90);
  const [refreshingNse, setRefreshingNse] = useState(false);
  const [nseStatus, setNseStatus] = useState<string | null>(null);
  const [nseError, setNseError] = useState<string | null>(null);

  const { data, error, loading, refreshing, refetch } = useApiResource<AnalyticsSummary>(
    (signal) => analyticsApi.summary(signal),
    [],
  );
  const owned = useApiResource<Stock[]>((signal) => portfolioApi.list(signal), []);
  const slb = useApiResource<SLBHistory>(
    (signal) => analyticsApi.history(selectedSymbol || null, days, signal),
    [selectedSymbol, days],
  );
  const equity = useApiResource<EquityHistory>(
    (signal) => analyticsApi.equityHistory(selectedSymbol, days, signal),
    [selectedSymbol, days],
    { enabled: selectedSymbol !== "" },
  );

  const ownedStocks = (owned.data ?? []).filter((s) => s.quantity > 0);
  const slbPoints = slb.data?.points ?? [];
  const equityData = equity.data;
  const scopeLabel = selectedSymbol || "Owned portfolio";

  const handleRefreshNse = useCallback(async () => {
    setRefreshingNse(true);
    setNseStatus(null);
    setNseError(null);
    try {
      const result = await analyticsApi.refreshEquityHistory(selectedSymbol || null, days);
      setNseStatus(
        `Fetched ${formatQty(result.total_bars)} bar${result.total_bars === 1 ? "" : "s"} across ${formatQty(result.symbols)} symbol${result.symbols === 1 ? "" : "s"}.`,
      );
      equity.refetch();
    } catch (err) {
      setNseError(toApiError(err).userMessage);
    } finally {
      setRefreshingNse(false);
    }
  }, [selectedSymbol, days, equity]);

  if (error && !data) {
    return (
      <Card className="panel-pad">
        <ErrorState error={error} onRetry={refetch} />
      </Card>
    );
  }

  const statusEntries = Object.entries(data?.status_breakdown ?? {});
  const fyRows = data?.income_by_financial_year ?? [];
  const yields = data?.yield_by_symbol ?? [];

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-slate-500">
          Read-only roll-ups over your persisted contracts, P&L and captured market data. No
          forecasts — figures reflect actual recorded data only.
        </p>
        <div className="flex items-center gap-2 no-print">
          <ExportCsvButton url={exportApi.analyticsCsvUrl()} />
          <button type="button" className="btn-ghost" onClick={() => window.print()}>
            Print / Save as PDF
          </button>
          <button type="button" className="btn-ghost" onClick={refetch} disabled={refreshing}>
            Refresh
          </button>
        </div>
      </div>

      {/* Contract status breakdown */}
      <Card>
        <SectionHeader title="Contract status" subtitle="Derived lifecycle state (IST, date-consistent)." />
        {loading ? (
          <TableSkeleton rows={1} cols={4} />
        ) : statusEntries.length === 0 ? (
          <EmptyState icon="📄" title="No contracts yet" message="Open contracts to see their lifecycle breakdown here." />
        ) : (
          <div className="flex flex-wrap gap-3 p-4">
            {statusEntries.map(([status, count]) => (
              <div key={status} className="rounded-md border border-line bg-surface-2/50 px-4 py-3">
                <p className="text-2xs uppercase tracking-wide text-slate-500">
                  {STATUS_LABELS[status] ?? status}
                </p>
                <p className="mt-1 text-xl font-semibold tnum text-slate-100">{formatQty(count)}</p>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* Income by financial year */}
      <Card>
        <SectionHeader title="Income by financial year" subtitle="Net lending income per Indian FY (Apr–Mar)." />
        {loading ? (
          <TableSkeleton rows={3} cols={4} />
        ) : fyRows.length === 0 ? (
          <EmptyState icon="₹" title="No P&L recorded yet" message="Compute P&L for contracts to populate the yearly income view." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr className="border-b border-line">
                  <th className="th">Financial year</th>
                  <th className="th text-right">Contracts</th>
                  <th className="th text-right">Gross fee</th>
                  <th className="th text-right">Net income</th>
                </tr>
              </thead>
              <tbody>
                {fyRows.map((fy) => (
                  <tr key={fy.financial_year} className="border-b border-line/50">
                    <td className="td font-medium">{fy.financial_year}</td>
                    <td className="td text-right tnum">{formatQty(fy.contracts)}</td>
                    <td className="td text-right tnum">{formatINR(fy.gross_fee)}</td>
                    <td className="td text-right tnum font-semibold text-pos">{formatINR(fy.net_income)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Yield by symbol */}
      <Card>
        <SectionHeader title="Yield by symbol" subtitle="Gross annualised yields observed in Series B snapshots. Current net yield at the bid is on the Market page." />
        {loading ? (
          <TableSkeleton rows={4} cols={3} />
        ) : yields.length === 0 ? (
          <EmptyState icon="📊" title="No market data yet" message="Fetch live SLB data on the Live SLB Data page to populate yield analytics." />
        ) : (
          <div className="space-y-4 p-4">
            <YieldChart data={yields} />
            <div className="overflow-x-auto">
              <table className="w-full border-collapse">
                <thead>
                  <tr className="border-b border-line">
                    <th className="th">Symbol</th>
                    <th className="th text-right">Latest yield</th>
                    <th className="th text-right">Average yield</th>
                    <th className="th text-right">Samples</th>
                  </tr>
                </thead>
                <tbody>
                  {yields.map((s) => (
                    <tr key={s.symbol} className="border-b border-line/50">
                      <td className="td font-medium">{s.symbol}</td>
                      <td className="td text-right tnum">
                        {s.latest_yield !== null ? formatPercent(s.latest_yield) : "—"}
                      </td>
                      <td className="td text-right tnum text-slate-400">
                        {s.average_yield !== null ? formatPercent(s.average_yield) : "—"}
                      </td>
                      <td className="td text-right tnum text-slate-500">{formatQty(s.samples)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </Card>

      {/* Historical trends (Phase 5B) */}
      <Card>
        <SectionHeader
          title="Historical trends"
          subtitle="Daily SLB yield trend and persisted equity price history. Equity history is fetched from NSE on demand — never forecast."
          right={
            <ExportCsvButton
              url={exportApi.historyCsvUrl(selectedSymbol || null, days)}
              title={`SLB yield trend CSV — ${scopeLabel}, ${days}d`}
            />
          }
        />
        <div className="space-y-5 p-4">
          {/* Controls */}
          <div className="flex flex-wrap items-end gap-3">
            <div>
              <label className="label" htmlFor="hist-symbol">
                Symbol
              </label>
              <select
                id="hist-symbol"
                className="input"
                value={selectedSymbol}
                onChange={(e) => setSelectedSymbol(e.target.value)}
              >
                <option value="">Portfolio (all owned)</option>
                {ownedStocks.map((s) => (
                  <option key={s.symbol} value={s.symbol}>
                    {s.symbol}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="label" htmlFor="hist-days">
                Window
              </label>
              <select
                id="hist-days"
                className="input"
                value={days}
                onChange={(e) => setDays(Number(e.target.value))}
              >
                {DAY_RANGE_OPTIONS.map((d) => (
                  <option key={d} value={d}>
                    {d} days
                  </option>
                ))}
              </select>
            </div>
            <button
              type="button"
              className="btn-primary"
              onClick={handleRefreshNse}
              disabled={refreshingNse}
            >
              {refreshingNse ? "Fetching…" : `Refresh ${selectedSymbol || "all"} from NSE`}
            </button>
          </div>

          {nseStatus ? <p className="text-2xs text-pos">{nseStatus}</p> : null}
          {nseError ? <p className="text-2xs text-neg">{nseError}</p> : null}

          {/* SLB yield trend (portfolio or per-symbol) */}
          <div className="space-y-2">
            <h3 className="text-xs font-semibold text-slate-200">SLB yield trend — {scopeLabel}</h3>
            {slb.loading ? (
              <TableSkeleton rows={3} cols={4} />
            ) : slb.error && !slb.data ? (
              <ErrorState error={slb.error} onRetry={slb.refetch} compact />
            ) : slbPoints.length === 0 ? (
              <EmptyState
                icon="📈"
                title="No snapshot history yet"
                message="Daily SLB trends build up as market snapshots are captured over time."
              />
            ) : (
              <>
                <YieldHistoryChart points={slbPoints} />
                {slb.data && slb.data.days < days ? (
                  <p className="text-2xs text-slate-500">
                    Showing {formatQty(slb.data.days)} days — SLB snapshot history is retained for a
                    limited window.
                  </p>
                ) : null}
              </>
            )}
          </div>

          {/* Equity price history (requires a selected symbol) */}
          <div className="space-y-2">
            <h3 className="text-xs font-semibold text-slate-200">Equity price history</h3>
            {selectedSymbol === "" ? (
              <EmptyState
                icon="🏷️"
                title="Pick a symbol"
                message="Select an owned symbol above to see its daily price/volume history and descriptive stats."
              />
            ) : equity.loading ? (
              <TableSkeleton rows={3} cols={6} />
            ) : equity.error && !equity.data ? (
              <ErrorState error={equity.error} onRetry={equity.refetch} compact />
            ) : !equityData || equityData.stats.samples === 0 ? (
              <EmptyState
                icon="📉"
                title="No price history stored"
                message={`No persisted bars for ${selectedSymbol}. Use "Refresh from NSE" above to fetch daily history.`}
              />
            ) : (
              <div className="space-y-4">
                <EquityStatsGrid stats={equityData.stats} />
                <PriceHistoryChart bars={equityData.bars} />
              </div>
            )}
          </div>
        </div>
      </Card>
    </div>
  );
}
