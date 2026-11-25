import { useState } from "react";
import { Link } from "react-router-dom";
import { opportunitiesApi } from "@/api/opportunities";
import { useApiResource } from "@/hooks/useApiResource";
import { Card, SectionHeader } from "./Card";
import { EmptyState, ErrorState, TableSkeleton } from "./StateViews";
import { CopyTicketButton } from "./CopyTicketButton";
import { formatDate, formatINR, formatPercent, formatPrice, formatQty } from "@/utils/format";
import type { OpportunityOption, OpportunityStock } from "@/types";

/** Best actionable option of a stock: highest net yield among actionable ones. */
function bestActionable(stock: OpportunityStock): OpportunityOption | undefined {
  return stock.options
    .filter((o) => o.actionable)
    .sort((a, b) => (b.net_yield_pa ?? -Infinity) - (a.net_yield_pa ?? -Infinity))[0];
}

function StockCard({ stock }: { stock: OpportunityStock }) {
  const best = bestActionable(stock);
  const [open, setOpen] = useState<string | null>(null);
  const selected = stock.options.find((o) => o.series_code === open);

  return (
    <div data-testid={`expiry-card-${stock.symbol}`} className="panel panel-pad space-y-2">
      <div className="flex items-baseline justify-between">
        <Link to={`/slb/${stock.symbol}`} className="text-sm font-semibold text-slate-100 hover:underline">
          {stock.symbol}
        </Link>
        <span className="text-2xs text-slate-500">
          lendable {formatQty(stock.lendable)}
          {stock.pledged ? ` · ${formatQty(stock.pledged)} pledged` : ""}
        </span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {stock.options.map((o) => {
          const isBest = best?.series_code === o.series_code;
          return (
            <button
              key={o.series_code}
              type="button"
              data-best={isBest ? "true" : "false"}
              aria-pressed={open === o.series_code}
              onClick={() => setOpen(open === o.series_code ? null : o.series_code)}
              className={`rounded-md border px-2 py-1 text-left text-2xs ${
                isBest ? "border-pos ring-2 ring-pos/60" : "border-line"
              } ${o.actionable ? "text-slate-100" : "text-slate-500"}`}
            >
              <span className="font-semibold">{o.series_code}</span> · {formatDate(o.settlement_date)}
              <span className="block tnum">
                {o.net_yield_pa != null ? formatPercent(o.net_yield_pa) : "—"} · {o.days_to_settlement} d
              </span>
            </button>
          );
        })}
      </div>
      {selected ? (
        <div className="rounded-md bg-surface-2/60 px-3 py-2 text-2xs text-slate-300">
          <p className="tnum">
            {selected.series_code} settles {formatDate(selected.settlement_date)} ({selected.days_to_settlement} d) ·
            bid ₹{formatPrice(selected.best_bid)} ({formatQty(selected.best_bid_qty)}) · fill{" "}
            {formatQty(selected.fillable)} · net {formatINR(selected.net_fee ?? 0)}
            {selected.net_yield_pa != null ? ` = ${formatPercent(selected.net_yield_pa)} p.a.` : ""}
            {selected.price_for_floor != null ? ` · needs ₹${formatPrice(selected.price_for_floor, 4)} for the floor` : ""}
          </p>
          {selected.actionable && selected.ticket ? (
            <div className="mt-1 flex items-center gap-2">
              <code className="text-slate-100">{selected.ticket}</code>
              <CopyTicketButton ticket={selected.ticket} />
            </div>
          ) : (
            <ul className="mt-1 list-disc pl-4 text-warn">
              {selected.reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}

/**
 * Expiries (Phase 6.2): one card per held stock with a chip per tracked Series
 * B expiry, priced at the live bid net of charges; the best actionable chip is
 * outlined, and tapping a chip opens its full details.
 */
export function ExpiriesView() {
  const board = useApiResource((signal) => opportunitiesApi.list(undefined, signal), []);
  const data = board.data;
  return (
    <Card>
      <SectionHeader
        title="Expiries"
        subtitle={
          data
            ? `Next ${data.expiries} Series B expiries per stock at the live bid, net of charges; floor ${formatPercent(data.min_net_yield_pct)}.`
            : "Next Series B expiries per stock at the live bid, net of charges."
        }
        right={
          <button type="button" className="btn-ghost" onClick={board.refetch} disabled={board.refreshing}>
            Refresh
          </button>
        }
      />
      {board.loading ? (
        <TableSkeleton rows={4} cols={3} />
      ) : board.error ? (
        <div className="p-4">
          <ErrorState error={board.error} onRetry={board.refetch} />
        </div>
      ) : !data || data.stocks.length === 0 ? (
        <EmptyState title="No Series B quotes yet" message="Expiries appear after the first sweep in market hours." />
      ) : (
        <div className="grid gap-3 p-4 md:grid-cols-2 xl:grid-cols-3">
          {[...data.stocks]
            .sort((a, b) => Number(Boolean(bestActionable(b))) - Number(Boolean(bestActionable(a))))
            .map((s) => (
              <StockCard key={s.symbol} stock={s} />
            ))}
        </div>
      )}
    </Card>
  );
}
