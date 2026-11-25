import { Fragment, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { slbApi } from "@/api/slb";
import { useApiResource } from "@/hooks/useApiResource";
import { Card, SectionHeader } from "@/components/Card";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/StateViews";
import {
  formatDate,
  formatPercent,
  formatPrice,
  formatQty,
  formatRelative,
} from "@/utils/format";
import { downloadCsv, isoDaysBefore, istToday, toCsv } from "@/utils/csv";
import type { DepthLevel, QuoteSeries, SeriesFamily, NSEHistory, SLBQuote } from "@/types";

/**
 * Per-stock SLB quote (Phase 6.3): every series with its A / B / special
 * badge, best bid and offer, 5-level depth, NSE's settlement date, and NSE's
 * daily SLB trades for 1W / 1M / 2M with a CSV export. When NSE fails the
 * backend serves the stored rows; the page says so and shows their age.
 */

const RANGES = [
  { label: "1W", days: 7 },
  { label: "1M", days: 30 },
  { label: "2M", days: 60 },
] as const;

const BADGE: Record<SeriesFamily, { text: string; cls: string; title: string }> = {
  B: { text: "B", cls: "bg-pos/15 text-pos", title: "Series B: what Zerodha lends" },
  A: { text: "A", cls: "bg-surface-3 text-slate-300", title: "Series A: foreclosed on AGM/EGM" },
  SPECIAL: { text: "Special", cls: "bg-warn/15 text-warn", title: "Special series: never lendable here" },
};

function FamilyBadge({ family }: { family: SeriesFamily | null }) {
  if (!family) return <span className="pill bg-surface-3 text-slate-500">?</span>;
  const b = BADGE[family];
  return (
    <span className={`pill ${b.cls}`} title={b.title}>
      {b.text}
    </span>
  );
}

function DepthTable({ side, levels }: { side: string; levels: DepthLevel[] }) {
  return (
    <div>
      <p className="mb-1 text-2xs font-semibold uppercase tracking-wide text-slate-500">{side}</p>
      {levels.length === 0 ? (
        <p className="text-2xs text-slate-500">None</p>
      ) : (
        <table className="w-full text-2xs">
          <tbody>
            {levels.map((l, i) => (
              <tr key={i}>
                <td className="tnum text-slate-300">{formatPrice(l.price)}</td>
                <td className="tnum text-right text-slate-400">{formatQty(l.quantity)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function cachedAge(quote: SLBQuote): string | null {
  const stamps = quote.series.map((s) => s.as_of).filter(Boolean) as string[];
  if (stamps.length === 0) return null;
  return formatRelative(stamps.sort().at(-1) ?? null);
}

export function SLBQuotePage() {
  const { symbol = "" } = useParams();
  const sym = symbol.toUpperCase();
  const quote = useApiResource<SLBQuote>((signal) => slbApi.quote(sym, signal), [sym]);
  const [range, setRange] = useState<(typeof RANGES)[number]>(RANGES[1]);
  const [open, setOpen] = useState<string | null>(null);
  const to = useMemo(() => istToday(), []);
  const from = isoDaysBefore(to, range.days);
  const history = useApiResource<NSEHistory>(
    (signal) => slbApi.history(sym, from, to, signal),
    [sym, from, to],
  );

  function exportCsv() {
    const rows = (history.data?.rows ?? []).filter(r=>r.series_family==="B");
    const text = toCsv(
      ["trade_date", "series", "family", "settlement_date", "close", "open", "high", "low", "quantity", "trades", "value"],
      rows.map((r) => [
        r.trade_date, r.series_code, r.series_family, r.settlement_date, r.close, r.open, r.high, r.low,
        r.quantity, r.trades, r.value,
      ]),
    );
    downloadCsv(`${sym}-slb-history-${from}-to-${to}.csv`, text);
  }

  const q = quote.data;
  const age = q && q.source !== "quote" ? cachedAge(q) : null;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-lg font-semibold text-slate-100">{sym}</p>
          <p className="text-2xs text-slate-500">
            {q?.company_name ?? "SLB quote"}
            {q?.as_of ? ` · NSE as of ${q.as_of}` : ""}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Link to="/live" className="btn-ghost">
            Live board
          </Link>
          <button type="button" className="btn-ghost" onClick={quote.refetch} disabled={quote.refreshing}>
            Refresh
          </button>
        </div>
      </div>

      {q && q.source !== "quote" ? (
        <div role="alert" className="rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-sm text-slate-200">
          NSE is unavailable{q.blocked ? " (blocked, backing off)" : ""}: showing{" "}
          {q.source === "cache" ? "cached data" : "the fallback feed"}
          {age ? ` from ${age}` : ""}. Depth is not available in this view.
        </div>
      ) : null}

      <Card>
        <SectionHeader title="Series" subtitle="Series B contracts only, with NSE settlement dates and order-book depth." />
        {quote.loading ? (
          <TableSkeleton rows={6} cols={9} />
        ) : quote.error && !q ? (
          <div className="p-4">
            <ErrorState error={quote.error} onRetry={quote.refetch} />
          </div>
        ) : !q || q.series.filter(s=>s.series_family==="B").length === 0 ? (
          <EmptyState title="No SLB series" message={`NSE returned no SLB series for ${sym}; it may not be on the SLB board.`} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr className="border-b border-line">
                  <th className="th">Series</th>
                  <th className="th">Family</th>
                  <th className="th">Settles</th>
                  <th className="th text-right">Last fee</th>
                  <th className="th text-right">Traded today</th>
                  <th className="th text-right">Open pos.</th>
                  <th className="th text-right">Best bid (qty)</th>
                  <th className="th text-right">Best offer (qty)</th>
                  <th className="th text-right">Yield p.a.</th>
                  <th className="th text-right">Depth</th>
                </tr>
              </thead>
              <tbody>
                {q.series.filter(s=>s.series_family==="B").map((s: QuoteSeries, i) => {
                  const key = s.series_code ?? `${s.month}-${i}`;
                  const hasDepth = Boolean(s.bids || s.offers);
                  return (
                    <Fragment key={key}>
                      <tr data-testid={`quote-row-${key}`} className="border-b border-line/50">
                        <td className="td font-semibold">
                          {s.series_code ?? "—"} <span className="text-2xs text-slate-500">{s.month}</span>
                        </td>
                        <td className="td">
                          <FamilyBadge family={s.series_family} />
                        </td>
                        <td className="td text-2xs text-slate-300">
                          {formatDate(s.settlement_date)}
                          {s.settlement_estimated ? <span title="Estimated (first-Tuesday rule)"> ~</span> : null}
                        </td>
                        <td className="td text-right tnum">{s.ltp ? formatPrice(s.ltp) : "—"}</td>
                        <td className="td text-right tnum">{formatQty(s.traded_quantity)}</td>
                        <td className="td text-right tnum">{formatQty(s.open_positions)}</td>
                        <td className="td text-right tnum">
                          {s.best_bid ? `${formatPrice(s.best_bid)} (${formatQty(s.best_bid_qty)})` : "—"}
                        </td>
                        <td className="td text-right tnum">
                          {s.best_ask ? `${formatPrice(s.best_ask)} (${formatQty(s.best_ask_qty)})` : "—"}
                        </td>
                        <td className="td text-right tnum">
                          {s.annualized_yield ? formatPercent(s.annualized_yield) : "—"}
                        </td>
                        <td className="td text-right">
                          {hasDepth ? (
                            <button
                              type="button"
                              aria-label={`Depth ${key}`}
                              className="rounded px-2 py-1 text-2xs text-slate-300 hover:bg-surface-3"
                              onClick={() => setOpen(open === key ? null : key)}
                            >
                              {open === key ? "Hide" : "Show"}
                            </button>
                          ) : null}
                        </td>
                      </tr>
                      {open === key ? (
                        <tr className="border-b border-line/50 bg-surface-2/40">
                          <td colSpan={10} className="px-4 py-2">
                            <div data-testid={`depth-${key}`} className="grid max-w-md grid-cols-2 gap-6">
                              <DepthTable side="Bids" levels={s.bids ?? []} />
                              <DepthTable side="Offers" levels={s.offers ?? []} />
                            </div>
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card>
        <SectionHeader
          title="NSE trade history"
          subtitle={`Daily SLB trades from NSE, ${from} to ${to}. NSE keeps about two months.`}
          right={
            <div className="flex items-center gap-1">
              {RANGES.map((r) => (
                <button
                  key={r.label}
                  type="button"
                  aria-pressed={r.label === range.label}
                  className={r.label === range.label ? "btn-primary" : "btn-ghost"}
                  onClick={() => setRange(r)}
                >
                  {r.label}
                </button>
              ))}
              <button
                type="button"
                className="btn-ghost"
                onClick={exportCsv}
                disabled={!history.data || history.data.rows.filter(r=>r.series_family==="B").length === 0}
              >
                Export CSV
              </button>
            </div>
          }
        />
        {history.loading ? (
          <TableSkeleton rows={4} cols={6} />
        ) : history.error ? (
          <div className="p-4">
            <ErrorState error={history.error} onRetry={history.refetch} />
          </div>
        ) : !history.data || history.data.rows.filter(r=>r.series_family==="B").length === 0 ? (
          <EmptyState title="No trades in this range" message="NSE reported no SLB trades for these dates." />
        ) : (
          <div className="max-h-96 overflow-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr className="border-b border-line">
                  <th className="th">Date</th>
                  <th className="th">Series</th>
                  <th className="th">Settles</th>
                  <th className="th text-right">Close</th>
                  <th className="th text-right">High / low</th>
                  <th className="th text-right">Qty</th>
                  <th className="th text-right">Trades</th>
                </tr>
              </thead>
              <tbody>
                {[...history.data.rows].filter(r=>r.series_family==="B").reverse().map((r, i) => (
                  <tr key={`${r.trade_date}-${r.series_code}-${i}`} className="border-b border-line/50">
                    <td className="td text-2xs text-slate-300">{formatDate(r.trade_date)}</td>
                    <td className="td">
                      {r.series_code} <FamilyBadge family={r.series_family} />
                    </td>
                    <td className="td text-2xs text-slate-400">{formatDate(r.settlement_date)}</td>
                    <td className="td text-right tnum">{formatPrice(r.close)}</td>
                    <td className="td text-right tnum text-2xs text-slate-400">
                      {formatPrice(r.high)} / {formatPrice(r.low)}
                    </td>
                    <td className="td text-right tnum">{formatQty(r.quantity)}</td>
                    <td className="td text-right tnum">{formatQty(r.trades)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
