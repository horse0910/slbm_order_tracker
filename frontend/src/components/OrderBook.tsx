import type { SLBQuoteItem } from "@/types";
import { formatPrice, formatQty } from "@/utils/format";
import { EmptyState } from "./StateViews";

/**
 * Top-of-book (Level 1) SLB order book.
 *
 * The Phase 1 backend exposes only the best bid/ask level (`buyOrderPrice1` /
 * `sellOrderPrice1` from NSE), so this renders the single top level rather than
 * fabricating five levels of depth the backend does not provide.
 */
export function OrderBook({ quote }: { quote: SLBQuoteItem }) {
  const hasBid = quote.best_bid > 0 || quote.best_bid_qty > 0;
  const hasAsk = quote.best_ask > 0 || quote.best_ask_qty > 0;

  if (!hasBid && !hasAsk) {
    return (
      <EmptyState
        icon="📭"
        title="No open orders"
        message="The exchange currently reports no bids or asks for this contract."
      />
    );
  }

  return (
    <div className="p-4">
      <div className="mb-2 flex items-center justify-between text-2xs text-slate-500">
        <span>Top of book (Level 1)</span>
        {quote.spread > 0 ? (
          <span className="tnum">
            Spread ₹{formatPrice(quote.spread)} ({formatPrice(quote.spread_pct)}%)
          </span>
        ) : null}
      </div>
      <div className="grid grid-cols-2 gap-3">
        {/* Bids — borrowers */}
        <div className="rounded-md border border-pos/30 bg-pos/5">
          <div className="border-b border-pos/20 px-3 py-1.5 text-2xs font-semibold uppercase tracking-wide text-pos">
            Best bid · Borrower
          </div>
          <div className="flex items-baseline justify-between px-3 py-3">
            <div>
              <p className="text-2xs text-slate-500">Fee / share</p>
              <p className="text-lg font-semibold tnum text-slate-100">
                {hasBid ? `₹${formatPrice(quote.best_bid)}` : "—"}
              </p>
            </div>
            <div className="text-right">
              <p className="text-2xs text-slate-500">Qty</p>
              <p className="tnum text-slate-200">{hasBid ? formatQty(quote.best_bid_qty) : "—"}</p>
            </div>
          </div>
        </div>

        {/* Asks — lenders */}
        <div className="rounded-md border border-neg/30 bg-neg/5">
          <div className="border-b border-neg/20 px-3 py-1.5 text-2xs font-semibold uppercase tracking-wide text-neg">
            Best ask · Lender
          </div>
          <div className="flex items-baseline justify-between px-3 py-3">
            <div>
              <p className="text-2xs text-slate-500">Fee / share</p>
              <p className="text-lg font-semibold tnum text-slate-100">
                {hasAsk ? `₹${formatPrice(quote.best_ask)}` : "—"}
              </p>
            </div>
            <div className="text-right">
              <p className="text-2xs text-slate-500">Qty</p>
              <p className="tnum text-slate-200">{hasAsk ? formatQty(quote.best_ask_qty) : "—"}</p>
            </div>
          </div>
        </div>
      </div>
      <p className="mt-2 text-2xs text-slate-600">
        Deeper depth (levels 2–5) is not exposed by the backend data source.
      </p>
    </div>
  );
}
