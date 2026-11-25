import { useEffect, useMemo, useState } from "react";
import { ExpiriesView } from "@/components/ExpiriesView";
import { portfolioApi } from "@/api/portfolio";
import { symbolsApi } from "@/api/symbols";
import { slbApi } from "@/api/slb";
import { useApiResource } from "@/hooks/useApiResource";
import { POLL_INTERVAL_MS } from "@/utils/config";
import { Card, SectionHeader } from "@/components/Card";
import { EmptyState, ErrorState, Skeleton, TableSkeleton } from "@/components/StateViews";
import { FreshnessIndicator } from "@/components/Freshness";
import { SLBContractDetail } from "@/components/SLBContractDetail";
import { latestTimestamp } from "@/utils/freshness";
import { formatDate } from "@/utils/format";
import type { SLBLiveResponse, Stock, SymbolUniverse } from "@/types";

function SymbolPicker({
  symbols,
  value,
  onChange,
}: {
  symbols: string[];
  value: string;
  onChange: (symbol: string) => void;
}) {
  return (
    <div>
      <label className="label" htmlFor="slb-symbol">
        Symbol
      </label>
      <select
        id="slb-symbol"
        className="input min-w-[200px]"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {symbols.map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>
    </div>
  );
}

function MarketView() {
  // Primary source for the selector: the configured/seeded tracked-symbol
  // universe. Portfolio holdings are unioned in so a symbol the user owns but
  // that is not in the universe stays monitorable (preserves prior behavior).
  const universe = useApiResource<SymbolUniverse>((signal) => symbolsApi.list(signal), []);
  const portfolio = useApiResource<Stock[]>((signal) => portfolioApi.list(signal), []);
  const [symbol, setSymbol] = useState<string>("");
  const [series, setSeries] = useState<string>("");

  // De-duplicated, alphabetically-sorted union of universe + owned symbols.
  const symbolOptions = useMemo(() => {
    const set = new Set<string>();
    for (const s of universe.data?.symbols ?? []) set.add(s.symbol);
    for (const s of portfolio.data ?? []) set.add(s.symbol);
    return [...set].sort((a, b) => a.localeCompare(b));
  }, [universe.data, portfolio.data]);

  // Default to a holding NSE reports as on the SLB board (so the first view has a
  // quote), else the first option once loaded (Phase 6.2; was ARCIL, no quote).
  useEffect(() => {
    if (symbol || symbolOptions.length === 0) return;
    const onBoard = (portfolio.data ?? [])
      .filter((s) => s.is_slb_eligible && s.quantity > 0)
      .map((s) => s.symbol)
      .sort((a, b) => a.localeCompare(b));
    if (portfolio.loading && onBoard.length === 0) return;
    setSymbol(onBoard[0] ?? symbolOptions[0]);
  }, [symbolOptions, symbol, portfolio.data, portfolio.loading]);

  const slb = useApiResource<SLBLiveResponse>(
    (signal) => slbApi.liveBySymbol(symbol, signal),
    [symbol],
    { pollIntervalMs: POLL_INTERVAL_MS, enabled: Boolean(symbol) },
  );

  const contracts = slb.data?.contracts ?? [];

  // Keep the selected series valid as contracts change; default to the first.
  useEffect(() => {
    if (contracts.length === 0) {
      if (series) setSeries("");
      return;
    }
    const stillValid = contracts.some((c) => c.series === series);
    if (!stillValid) setSeries(contracts[0].series);
  }, [contracts, series]);

  const selected = useMemo(
    () => contracts.find((c) => c.series === series) ?? contracts[0],
    [contracts, series],
  );

  const pageTimestamp = latestTimestamp(contracts.map((c) => c.timestamp));

  // Still resolving the symbol list for the first time.
  const symbolsLoading = universe.loading && portfolio.loading && symbolOptions.length === 0;

  // Both sources failed and we have nothing to select — cannot proceed.
  if (universe.error && portfolio.error && symbolOptions.length === 0) {
    return (
      <Card className="panel-pad">
        <ErrorState error={universe.error} onRetry={universe.refetch} />
      </Card>
    );
  }

  if (!symbolsLoading && !universe.loading && !portfolio.loading && symbolOptions.length === 0) {
    return (
      <Card>
        <EmptyState
          icon="📊"
          title="No symbols to monitor"
          message="The tracked-symbol universe is empty. Seed it (or add stocks to your portfolio) to view live SLB market data."
        />
      </Card>
    );
  }

  return (
    <div className="space-y-5">
      {/* Controls */}
      <Card className="panel-pad">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="flex flex-wrap items-end gap-4">
            {symbolsLoading ? (
              <Skeleton className="h-9 w-52" />
            ) : (
              <SymbolPicker symbols={symbolOptions} value={symbol} onChange={setSymbol} />
            )}
          </div>
          <div className="flex items-center gap-3">
            <FreshnessIndicator
              timestamp={pageTimestamp}
              lastUpdated={slb.lastUpdated}
              refreshing={slb.refreshing}
            />
            <button
              type="button"
              className="btn-ghost"
              onClick={slb.refetch}
              disabled={slb.refreshing || !symbol}
            >
              Refresh
            </button>
          </div>
        </div>

        {/* Expiry / series tabs */}
        {contracts.length > 0 ? (
          <div className="mt-4 flex flex-wrap gap-2 border-t border-line pt-3">
            {contracts.map((c) => {
              const active = c.series === (selected?.series ?? "");
              return (
                <button
                  key={`${c.series}-${c.expiry_date}`}
                  type="button"
                  onClick={() => setSeries(c.series)}
                  className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                    active
                      ? "bg-accent text-white"
                      : "border border-line bg-surface-2 text-slate-300 hover:bg-surface-3"
                  }`}
                >
                  {c.series || "—"}
                  <span className="ml-1 text-2xs opacity-70">{formatDate(c.expiry_date)}</span>
                </button>
              );
            })}
          </div>
        ) : null}
      </Card>

      {/* Data area */}
      {symbolsLoading || !symbol ? (
        <Card>
          <SectionHeader title="Loading market data…" />
          <TableSkeleton rows={4} cols={3} />
        </Card>
      ) : slb.error && !slb.data ? (
        <Card className="panel-pad">
          <ErrorState error={slb.error} onRetry={slb.refetch} />
        </Card>
      ) : slb.loading ? (
        <Card>
          <SectionHeader title="Loading market data…" />
          <TableSkeleton rows={4} cols={3} />
        </Card>
      ) : contracts.length === 0 ? (
        <Card>
          <EmptyState
            icon="🔍"
            title={`No SLB contracts for ${symbol}`}
            message="The exchange has no active SLB series for this symbol right now, or live data is temporarily unavailable. Try another symbol or refresh shortly."
          />
        </Card>
      ) : selected ? (
        <>
          {slb.error ? (
            <ErrorState error={slb.error} onRetry={slb.refetch} compact />
          ) : null}
          <SLBContractDetail quote={selected} />
        </>
      ) : null}
    </div>
  );
}

const TABS = ["Market", "Expiries"] as const;

/** Live SLB Data: the per-symbol market view and (Phase 6.2) the Expiries view. */
export function LiveSLBPage() {
  const [tab, setTab] = useState<(typeof TABS)[number]>("Market");
  return (
    <div className="space-y-4">
      <div className="flex gap-1" role="tablist" aria-label="Live SLB view">
        {TABS.map((t) => (
          <button
            key={t}
            type="button"
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`rounded-md px-2.5 py-1 text-2xs font-medium ${
              tab === t ? "bg-accent text-white" : "border border-line bg-surface-2 text-slate-300 hover:bg-surface-3"
            }`}
          >
            {t}
          </button>
        ))}
      </div>
      {tab === "Market" ? <MarketView /> : <ExpiriesView />}
    </div>
  );
}
