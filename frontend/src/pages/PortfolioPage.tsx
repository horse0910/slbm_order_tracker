import { useState } from "react";
import { Link } from "react-router-dom";
import { portfolioApi } from "@/api/portfolio";
import { ApiError, toApiError } from "@/api/client";
import { useApiResource } from "@/hooks/useApiResource";
import { Card, SectionHeader } from "@/components/Card";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/StateViews";
import { EligibilityPill } from "@/components/StatusPill";
import { Modal } from "@/components/Modal";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { StockForm } from "@/components/StockForm";
import { PortfolioImport } from "@/components/PortfolioImport";
import { formatCompactINR, formatDate, formatPrice, formatQty } from "@/utils/format";
import type { Holding, Stock, StockCreate } from "@/types";

type Dialog =
  | { kind: "closed" }
  | { kind: "add" }
  | { kind: "edit"; stock: Stock }
  | { kind: "import" }
  | { kind: "delete"; stock: Stock };

export function PortfolioPage() {
  const { data, error, loading, refreshing, refetch } = useApiResource<Holding[]>(
    (signal) => portfolioApi.list(signal),
    [],
  );

  const [dialog, setDialog] = useState<Dialog>({ kind: "closed" });
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<ApiError | null>(null);

  const close = () => {
    setDialog({ kind: "closed" });
    setFormError(null);
  };

  async function handleCreate(payload: StockCreate) {
    setSubmitting(true);
    setFormError(null);
    try {
      await portfolioApi.create(payload);
      close();
      refetch();
    } catch (err) {
      setFormError(toApiError(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleUpdate(stock: Stock, payload: StockCreate) {
    setSubmitting(true);
    setFormError(null);
    try {
      await portfolioApi.update(stock.id, {
        name: payload.name,
        isin: payload.isin,
        quantity: payload.quantity,
        avg_buy_price: payload.avg_buy_price,
        is_slb_eligible: payload.is_slb_eligible,
        notes: payload.notes,
      });
      close();
      refetch();
    } catch (err) {
      setFormError(toApiError(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete(stock: Stock) {
    setSubmitting(true);
    try {
      await portfolioApi.remove(stock.id);
      close();
      refetch();
    } catch (err) {
      setFormError(toApiError(err));
    } finally {
      setSubmitting(false);
    }
  }

  const stocks = data ?? [];

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <p className="text-xs text-slate-500">
          Manage tracked holdings. Live prices and lending quotes are on the Live SLB Data page.
        </p>
        <div className="flex gap-2">
          <button type="button" className="btn-ghost" onClick={() => setDialog({ kind: "import" })}>
            Import CSV
          </button>
          <button type="button" className="btn-primary" onClick={() => setDialog({ kind: "add" })}>
            + Add stock
          </button>
        </div>
      </div>

      <Card>
        <SectionHeader
          title="Portfolio holdings"
          subtitle={data ? `${stocks.length} stock${stocks.length === 1 ? "" : "s"} tracked` : undefined}
          right={
            <button type="button" className="btn-ghost" onClick={refetch} disabled={refreshing}>
              Refresh
            </button>
          }
        />

        {loading ? (
          <TableSkeleton rows={6} cols={9} />
        ) : error ? (
          <div className="p-4">
            <ErrorState error={error} onRetry={refetch} />
          </div>
        ) : stocks.length === 0 ? (
          <EmptyState
            icon="📈"
            title="No holdings yet"
            message="Add a stock manually or import your Zerodha holdings CSV to start tracking SLB opportunities."
            action={
              <div className="flex gap-2">
                <button
                  type="button"
                  className="btn-ghost"
                  onClick={() => setDialog({ kind: "import" })}
                >
                  Import CSV
                </button>
                <button
                  type="button"
                  className="btn-primary"
                  onClick={() => setDialog({ kind: "add" })}
                >
                  Add stock
                </button>
              </div>
            }
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr className="border-b border-line">
                  <th className="th">Symbol</th>
                  <th className="th">Name</th>
                  <th className="th">ISIN</th>
                  <th className="th text-right" title="Imported quantity minus shares lent since that import">
                    In demat
                  </th>
                  <th className="th text-right" title="Under ACTIVE contracts (left out of Zerodha's export)">
                    Lent out
                  </th>
                  <th className="th text-right">Total owned</th>
                  <th className="th text-right" title="In demat minus pledged: what can be lent now">
                    Lendable
                  </th>
                  <th className="th text-right">Avg. price</th>
                  <th className="th text-right">Cost value</th>
                  <th className="th">SLB</th>
                  <th className="th text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {stocks.map((s) => (
                  <tr key={s.id} className="border-b border-line/50 hover:bg-surface-2/40">
                    <td className="td">
                      <Link to={`/slb/${s.symbol}`} className="font-semibold hover:text-accent hover:underline">
                        {s.symbol}
                      </Link>
                      {s.lent_contracts.map((c) => (
                        <span
                          key={c.contract_id}
                          className="pill mt-1 mr-1 bg-accent-soft text-2xs text-slate-100"
                        >
                          Lent {formatQty(c.quantity_lent)} · {c.series ?? "—"} · settles{" "}
                          {formatDate(c.settlement_date)}
                        </span>
                      ))}
                    </td>
                    <td className="td max-w-[220px] truncate text-slate-300">{s.name ?? "—"}</td>
                    <td className="td text-2xs text-slate-500">{s.isin ?? "—"}</td>
                    <td className="td text-right tnum">{formatQty(s.in_demat)}</td>
                    <td className={`td text-right tnum ${s.lent > 0 ? "" : "text-slate-500"}`}>
                      {formatQty(s.lent)}
                    </td>
                    <td className="td text-right tnum font-medium">{formatQty(s.total_owned)}</td>
                    <td className="td text-right tnum">
                      {formatQty(s.lendable ?? s.in_demat)}
                      {s.pledged_quantity ? (
                        <span className="block text-2xs text-slate-500">{formatQty(s.pledged_quantity)} pledged</span>
                      ) : null}
                    </td>
                    <td className="td text-right tnum">₹{formatPrice(s.avg_buy_price)}</td>
                    <td className="td text-right tnum">
                      {formatCompactINR(s.total_owned * s.avg_buy_price)}
                    </td>
                    <td className="td">
                      <EligibilityPill eligible={s.is_slb_eligible} />
                    </td>
                    <td className="td text-right">
                      <div className="flex justify-end gap-1">
                        <button
                          type="button"
                          className="rounded px-2 py-1 text-2xs text-slate-300 hover:bg-surface-3"
                          onClick={() => setDialog({ kind: "edit", stock: s })}
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          className="rounded px-2 py-1 text-2xs text-neg hover:bg-neg/15"
                          onClick={() => setDialog({ kind: "delete", stock: s })}
                        >
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Add / Edit */}
      <Modal
        open={dialog.kind === "add" || dialog.kind === "edit"}
        title={dialog.kind === "edit" ? `Edit ${dialog.stock.symbol}` : "Add stock"}
        onClose={close}
      >
        {dialog.kind === "add" ? (
          <StockForm
            submitting={submitting}
            error={formError}
            onSubmit={handleCreate}
            onCancel={close}
          />
        ) : dialog.kind === "edit" ? (
          <StockForm
            initial={dialog.stock}
            submitting={submitting}
            error={formError}
            onSubmit={(payload) => handleUpdate(dialog.stock, payload)}
            onCancel={close}
          />
        ) : null}
      </Modal>

      {/* Import */}
      <Modal open={dialog.kind === "import"} title="Import holdings CSV" onClose={close}>
        <PortfolioImport onImported={refetch} />
      </Modal>

      {/* Delete */}
      <ConfirmDialog
        open={dialog.kind === "delete"}
        title="Remove stock"
        message={
          dialog.kind === "delete"
            ? `Remove ${dialog.stock.symbol} from your portfolio? This also deletes its tracked contracts and alerts. This cannot be undone.`
            : ""
        }
        confirmLabel="Delete"
        destructive
        busy={submitting}
        onCancel={close}
        onConfirm={() => dialog.kind === "delete" && handleDelete(dialog.stock)}
      />
    </div>
  );
}
