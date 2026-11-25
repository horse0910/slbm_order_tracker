import type { SLBQuoteItem } from "@/types";
import { Card, SectionHeader } from "./Card";
import { OrderBook } from "./OrderBook";
import { formatDate, formatPercent, formatPrice, formatQty } from "@/utils/format";

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-line bg-surface-0 px-3 py-2">
      <p className="text-2xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-0.5 text-sm font-medium tnum text-slate-100">{value}</p>
    </div>
  );
}

/** Annualized yield hero card — uses the backend's own calculated value. */
function YieldCard({ quote }: { quote: SLBQuoteItem }) {
  return (
    <div className="panel panel-pad flex flex-col justify-between bg-gradient-to-br from-surface-1 to-surface-2">
      <p className="text-2xs font-medium uppercase tracking-wide text-slate-500">
        Annualized yield (NSE)
      </p>
      <p className="mt-1 text-4xl font-semibold tnum text-pos">
        {formatPercent(quote.annualized_yield)}
      </p>
      <p className="mt-1 text-2xs text-slate-500">
        Lending fee ₹{formatPrice(quote.ltp)} / share · {quote.series || "—"}
      </p>
    </div>
  );
}

export function SLBContractDetail({ quote }: { quote: SLBQuoteItem }) {
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <YieldCard quote={quote} />
        <div className="lg:col-span-2">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <Stat label="Lending fee (LTP)" value={`₹${formatPrice(quote.ltp)}`} />
            <Stat label="Underlying price" value={`₹${formatPrice(quote.underlying_price)}`} />
            <Stat label="Expiry" value={formatDate(quote.expiry_date)} />
            <Stat label="Open positions" value={formatQty(quote.open_positions)} />
            <Stat label="Volume" value={formatQty(quote.volume)} />
            <Stat label="Turnover" value={`₹${formatPrice(quote.turnover)}`} />
          </div>
          {quote.corporate_action ? (
            <div className="mt-3 rounded-md border border-warn/30 bg-warn/10 px-3 py-2 text-2xs text-warn">
              ⚠ Corporate action: {quote.corporate_action}
            </div>
          ) : null}
        </div>
      </div>

      <Card>
        <SectionHeader
          title="Order book"
          subtitle={`${quote.symbol} · ${quote.series || "—"} · top of book (Level 1 only)`}
        />
        <OrderBook quote={quote} />
      </Card>
    </div>
  );
}
