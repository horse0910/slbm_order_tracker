import { useState, type FormEvent } from "react";
import { pnlApi } from "@/api/pnl";
import { exportApi } from "@/api/export";
import { toApiError } from "@/api/client";
import { useApiResource } from "@/hooks/useApiResource";
import { Card, SectionHeader } from "@/components/Card";
import { ExportCsvButton } from "@/components/ExportButton";
import { StatCard } from "@/components/StatCard";
import { EmptyState, ErrorState, Spinner, TableSkeleton } from "@/components/StateViews";
import { formatCompactINR, formatINR, formatPercent } from "@/utils/format";
import type { PnLBreakdown, PnLList } from "@/types";

/** Stateless P&L calculator that calls the backend engine (no client math). */
function PnLPreview() {
  const [qty, setQty] = useState("100");
  const [fee, setFee] = useState("5.00");
  const [tax, setTax] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<PnLBreakdown | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const r = await pnlApi.preview(
        Number(qty),
        Number(fee),
        tax.trim() === "" ? undefined : Number(tax),
      );
      setResult(r);
    } catch (e2) {
      setErr(toApiError(e2).userMessage);
    } finally {
      setBusy(false);
    }
  }

  const rows: [string, number][] = result
    ? [
        ["Gross fee", result.gross_fee],
        [`Processing (${formatPercent(result.processing_fee_pct, 0)})`, -result.processing_charge],
        [`GST (${formatPercent(result.gst_pct, 0)})`, -result.gst],
        [`DP charge (₹${result.dp_charge_flat_inr} + GST per scrip)`, -result.dp_charge],
        ["Net income", result.net_income],
        [`Estimated tax (${formatPercent(result.tax_slab_pct, 0)})`, -result.estimated_tax],
        ["Post-tax income", result.post_tax_income],
      ]
    : [];

  return (
    <Card>
      <SectionHeader title="Breakdown calculator" subtitle="Computed by the backend P&L engine." />
      <div className="grid gap-4 p-4 md:grid-cols-2">
        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label" htmlFor="pnl-qty">
                Quantity lent
              </label>
              <input
                id="pnl-qty"
                type="number"
                min={0}
                className="input tnum"
                value={qty}
                onChange={(e) => setQty(e.target.value)}
              />
            </div>
            <div>
              <label className="label" htmlFor="pnl-fee">
                Fee / share (₹)
              </label>
              <input
                id="pnl-fee"
                type="number"
                min={0}
                step="0.001"
                className="input tnum"
                value={fee}
                onChange={(e) => setFee(e.target.value)}
              />
            </div>
          </div>
          <div>
            <label className="label" htmlFor="pnl-tax">
              Tax slab % (estimate)
            </label>
            <input
              id="pnl-tax"
              type="number"
              min={0}
              max={100}
              step="0.01"
              className="input tnum"
              value={tax}
              onChange={(e) => setTax(e.target.value)}
              placeholder="Configured default"
            />
          </div>
          <button type="submit" className="btn-primary" disabled={busy}>
            {busy ? <Spinner /> : "Calculate"}
          </button>
          {err ? <p className="text-2xs text-neg">{err}</p> : null}
        </form>

        <div>
          {result ? (
            <table className="w-full border-collapse text-sm">
              <tbody>
                {rows.map(([label, value]) => {
                  const emphasize = label === "Net income" || label === "Post-tax income";
                  return (
                    <tr
                      key={label}
                      className={`border-b border-line/50 ${emphasize ? "font-semibold text-slate-100" : "text-slate-300"}`}
                    >
                      <td className="py-1.5">{label}</td>
                      <td className={`py-1.5 text-right tnum ${value < 0 ? "text-neg" : ""}`}>
                        {formatINR(value)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : (
            <p className="text-xs text-slate-500">
              Enter a quantity and fee to see the itemized breakdown. The tax line is an estimate,
              not an actual liability.
            </p>
          )}
        </div>
      </div>
    </Card>
  );
}

export function PnLPage() {
  const { data, error, loading, refreshing, refetch } = useApiResource<PnLList>(
    (signal) => pnlApi.list(signal),
    [],
  );

  const totals = data?.totals;
  const records = data?.records ?? [];

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-slate-500">
          Lending P&L by contract. Every figure is computed on the backend in exact rupee arithmetic;
          the tax line is a configurable estimate, not an actual liability.
        </p>
        <div className="flex items-center gap-2">
          <ExportCsvButton url={exportApi.pnlCsvUrl()} />
          <button type="button" className="btn-ghost" onClick={refetch} disabled={refreshing}>
            Refresh
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Gross fee" value={formatCompactINR(totals?.gross_fee ?? 0)} loading={loading} />
        <StatCard
          label="Net income"
          value={formatCompactINR(totals?.net_income ?? 0)}
          hint={`across ${totals?.records ?? 0} contract(s)`}
          loading={loading}
          accent="pos"
        />
        <StatCard
          label="Estimated tax"
          value={formatCompactINR(totals?.estimated_tax ?? 0)}
          hint="estimate only"
          loading={loading}
          accent="warn"
        />
        <StatCard
          label="Post-tax income"
          value={formatCompactINR(totals?.post_tax_income ?? 0)}
          loading={loading}
          accent="pos"
        />
      </div>

      <PnLPreview />

      <Card>
        <SectionHeader title="Per-contract P&L" subtitle="Persisted records." />
        {loading ? (
          <TableSkeleton rows={4} cols={6} />
        ) : error ? (
          <div className="p-4">
            <ErrorState error={error} onRetry={refetch} />
          </div>
        ) : records.length === 0 ? (
          <EmptyState
            icon="₹"
            title="No P&L records yet"
            message="Compute P&L for a contract (POST it from the Contracts workflow) to see the itemized breakdown and totals here."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr className="border-b border-line">
                  <th className="th">Symbol</th>
                  <th className="th">Series</th>
                  <th className="th text-right">Gross</th>
                  <th className="th text-right">Processing</th>
                  <th className="th text-right">GST</th>
                  <th className="th text-right">DP</th>
                  <th className="th text-right">Net</th>
                  <th className="th text-right">Est. tax</th>
                  <th className="th text-right">Post-tax</th>
                </tr>
              </thead>
              <tbody>
                {records.map((r) => (
                  <tr key={r.id} className="border-b border-line/50 hover:bg-surface-2/40">
                    <td className="td font-semibold">{r.symbol}</td>
                    <td className="td text-slate-400">{r.series ?? "—"}</td>
                    <td className="td text-right tnum">{formatINR(r.gross_fee)}</td>
                    <td className="td text-right tnum text-slate-400">{formatINR(r.processing_charge)}</td>
                    <td className="td text-right tnum text-slate-400">{formatINR(r.gst)}</td>
                    <td className="td text-right tnum text-slate-400">{formatINR(r.dp_charge)}</td>
                    <td className="td text-right tnum font-semibold text-pos">{formatINR(r.net_income)}</td>
                    <td className="td text-right tnum text-warn">{formatINR(r.estimated_tax)}</td>
                    <td className="td text-right tnum font-semibold">{formatINR(r.post_tax_income)}</td>
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
