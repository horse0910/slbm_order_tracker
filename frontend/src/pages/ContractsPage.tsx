import { useMemo, useState } from "react";
import { contractsApi } from "@/api/contracts";
import { portfolioApi } from "@/api/portfolio";
import { exportApi } from "@/api/export";
import { ApiError, toApiError } from "@/api/client";
import { useApiResource } from "@/hooks/useApiResource";
import { Card, SectionHeader } from "@/components/Card";
import { ExportCsvButton } from "@/components/ExportButton";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/StateViews";
import { ContractStatusPill, DaysBadge } from "@/components/StatusPill";
import { Modal } from "@/components/Modal";
import { ContractForm } from "@/components/ContractForm";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { RollDialog } from "@/components/RollDialog";
import { formatCompactINR, formatDate, formatPrice, formatQty } from "@/utils/format";
import type { Contract, ContractCreate, ContractStatus, Stock } from "@/types";

/** Stored statuses that can still be closed, recalled or rolled. */
const OPEN_STATUSES = new Set(["ACTIVE", "EXPIRED"]);

/** Status filter options. `null` = all. Order matches lifecycle progression. */
const FILTERS: { label: string; value: ContractStatus | null }[] = [
  { label: "All", value: null },
  { label: "Active", value: "ACTIVE" },
  { label: "Expiring soon", value: "EXPIRING_SOON" },
  { label: "Expired", value: "EXPIRED" },
];

export function ContractsPage() {
  const [query,setQuery]=useState("");
  const [expiry,setExpiry]=useState("");
  const [filter, setFilter] = useState<ContractStatus | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<ApiError | null>(null);
  const [saveWarning, setSaveWarning] = useState<string | null>(null);
  // Phase 6.4: one action dialog at a time.
  const [action, setAction] = useState<{ kind: "close" | "recall" | "roll"; contract: Contract } | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  async function runAction() {
    if (!action || action.kind === "roll") return;
    setActionBusy(true);
    setActionError(null);
    try {
      await (action.kind === "close" ? contractsApi.close : contractsApi.recall)(action.contract.id);
      setAction(null);
      refetch();
    } catch (err) {
      setActionError(toApiError(err).userMessage);
    } finally {
      setActionBusy(false);
    }
  }

  // Contracts refetch whenever the status filter changes (server-side filter).
  const { data, error, loading, refreshing, refetch } = useApiResource<Contract[]>(
    (signal) => contractsApi.list(filter ?? undefined, signal),
    [filter],
    {pollIntervalMs:60000},
  );

  // Portfolio holdings are needed to open a contract (must reference a Stock).
  const portfolio = useApiResource<Stock[]>((signal) => portfolioApi.list(signal), []);
  const stocks = useMemo(() => portfolio.data ?? [], [portfolio.data]);

  const close = () => {
    setDialogOpen(false);
    setFormError(null);
  };

  async function handleCreate(payload: ContractCreate) {
    setSubmitting(true);
    setFormError(null);
    setSaveWarning(null);
    try {
      const created = await contractsApi.create(payload);
      setSaveWarning(created.warning ?? null);
      close();
      refetch();
    } catch (err) {
      setFormError(toApiError(err));
    } finally {
      setSubmitting(false);
    }
  }

  const allContracts = data ?? [];
  const contracts = allContracts.filter(c=>(!expiry||c.expiry_date===expiry)&&(`${c.symbol} ${stocks.find(s=>s.id===c.stock_id)?.name||""}`).toLowerCase().includes(query.toLowerCase()));
  const expiryOptions=[...new Set(allContracts.map(c=>c.expiry_date))].sort();
  const canOpen = stocks.length > 0;

  return (
    <div className="space-y-5">
      <section className="market-hero"><div><p className="eyebrow">YOUR EXECUTED LENDING POSITIONS</p><h2>My contracts<span>.</span></h2><p>Agreed fees, expected net income and the journey back to your demat.</p></div></section>
      {saveWarning ? (
        <div
          role="status"
          className="flex items-start justify-between gap-3 rounded-md border border-warn/40 bg-warn/10 px-3 py-2"
        >
          <p className="text-sm text-slate-200">Contract saved. {saveWarning}</p>
          <button
            type="button"
            className="btn-ghost shrink-0"
            onClick={() => setSaveWarning(null)}
          >
            Dismiss
          </button>
        </div>
      ) : null}
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-slate-500">
          Track trades already executed through Zerodha. Shares, fees and rollover chains stay together; settlement completion is automatic. Net income estimates exclude tax.
        </p>
        <div className="flex items-center gap-2">
          <ExportCsvButton url={exportApi.contractsCsvUrl()} />
          <button
            type="button"
            className="btn-primary"
            onClick={() => setDialogOpen(true)}
            disabled={!canOpen}
            title={canOpen ? undefined : "Add a portfolio holding first"}
          >
            + Open contract
          </button>
        </div>
      </div>

      <Card className="panel-pad"><div className="flex flex-wrap gap-3"><div className="min-w-[200px] flex-1"><label className="label" htmlFor="contract-search">Search contracts</label><input id="contract-search" className="input" placeholder="Stock name or symbol…" value={query} onChange={e=>setQuery(e.target.value)}/></div><div><label className="label" htmlFor="contract-expiry">Settlement date</label><select id="contract-expiry" className="input" value={expiry} onChange={e=>setExpiry(e.target.value)}><option value="">All expiries</option>{expiryOptions.map(d=><option key={d} value={d}>{formatDate(d)}</option>)}</select></div></div></Card>
      <Card>
        <SectionHeader
          title="Lending contracts"
          subtitle={data ? `${contracts.length} contract${contracts.length === 1 ? "" : "s"}` : undefined}
          right={
            <div className="flex items-center gap-2">
              <div className="flex gap-1" role="tablist" aria-label="Filter by status">
                {FILTERS.map((f) => {
                  const active = f.value === filter;
                  return (
                    <button
                      key={f.label}
                      type="button"
                      role="tab"
                      aria-selected={active}
                      onClick={() => setFilter(f.value)}
                      className={`rounded-md px-2.5 py-1 text-2xs font-medium transition-colors ${
                        active
                          ? "bg-accent text-white"
                          : "border border-line bg-surface-2 text-slate-300 hover:bg-surface-3"
                      }`}
                    >
                      {f.label}
                    </button>
                  );
                })}
              </div>
              <button type="button" className="btn-ghost" onClick={refetch} disabled={refreshing}>
                Refresh
              </button>
            </div>
          }
        />

        {loading ? (
          <TableSkeleton rows={6} cols={7} />
        ) : error ? (
          <div className="p-4">
            <ErrorState error={error} onRetry={refetch} />
          </div>
        ) : contracts.length === 0 ? (
          <EmptyState
            icon="📄"
            title={filter ? "No contracts in this state" : "No lending contracts yet"}
            message={
              filter
                ? "Try a different status filter, or open a new contract."
                : canOpen
                  ? "Open a contract against one of your holdings to start tracking its SLB lifecycle and fee income."
                  : "Add a portfolio holding first, then open a lending contract against it."
            }
            action={
              canOpen ? (
                <button type="button" className="btn-primary" onClick={() => setDialogOpen(true)}>
                  Open contract
                </button>
              ) : undefined
            }
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr className="border-b border-line">
                  <th className="th">Symbol</th>
                  <th className="th">Series</th>
                  <th className="th">Status</th>
                  <th className="th text-right">Qty lent</th>
                  <th className="th text-right">Fee/share</th>
                  <th className="th text-right">Gross income</th>
                  <th className="th text-right">Est. charges</th>
                  <th className="th text-right">Net income</th>
                  <th className="th">Rollover history</th>
                  <th className="th">Expiry</th>
                  <th className="th text-right">Days left</th>
                  <th className="th text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {contracts.map((c) => (
                  <tr key={c.id} className="border-b border-line/50 hover:bg-surface-2/40">
                    <td className="td font-semibold">
                      {c.symbol}
                      {c.is_rollover_candidate ? (
                        <span
                          className="ml-1.5 pill bg-warn/20 text-warn"
                          title="Nearing expiry — consider rollover"
                        >
                          ⟳ Rollover
                        </span>
                      ) : null}
                    </td>
                    <td className="td text-slate-400">{c.series ?? "—"}</td>
                    <td className="td">
                      <ContractStatusPill status={c.effective_status} />
                    </td>
                    <td className="td text-right tnum">{formatQty(c.quantity_lent)}</td>
                    <td className="td text-right tnum">₹{formatPrice(c.lending_fee, 3)}</td>
                    <td className="td text-right tnum">{formatCompactINR(c.lending_fee_income)}</td>
                    <td className="td text-right tnum">{formatCompactINR(c.estimated_charges)}</td>
                    <td className="td text-right tnum text-pos">{formatCompactINR(c.net_income)}</td>
                    <td className="td"><details><summary className="cursor-pointer text-xs text-accent">{c.rollover_history && c.rollover_history.length>1?`${c.rollover_history.length} linked contracts`:"Original contract"}</summary><div className="mt-2 space-y-2">{(c.rollover_history||[]).map(item=><div key={item.id} className="text-2xs"><b>#{item.id} {item.series_code}</b> · {formatQty(item.quantity)} shares<br/>{formatDate(item.trade_date)} → {formatDate(item.settlement_date)} · {item.status}</div>)}{c.origin_date?<p className="text-2xs">Origin {formatDate(c.origin_date)} · {c.chain_days} of 365 days</p>:null}</div></details></td>
                    <td className="td text-slate-300">
                      {formatDate(c.reverse_leg_date||c.expiry_date)}
                      <span className="ml-1 text-2xs text-slate-500">({c.tenure_days}d term)</span>
                    </td>
                    <td className="td text-right">
                      {c.days_remaining >= 0 ? (
                        <DaysBadge days={c.days_remaining} />
                      ) : (
                        <span className="pill bg-surface-3 text-slate-500">
                          {Math.abs(c.days_remaining)}d ago
                        </span>
                      )}
                    </td>
                    <td className="td text-right">
                      {OPEN_STATUSES.has(c.stored_status) ? (
                        <div className="flex justify-end gap-1">
                          {(["close", "recall", "roll"] as const).map((kind) => (
                            <button
                              key={kind}
                              type="button"
                              aria-label={`${kind[0].toUpperCase()}${kind.slice(1)} ${c.symbol}`}
                              className={`rounded px-2 py-1 text-2xs hover:bg-surface-3 ${
                                kind === "recall" ? "text-warn" : "text-slate-300"
                              }`}
                              onClick={() => {
                                setActionError(null);
                                setAction({ kind, contract: c });
                              }}
                            >
                              {kind[0].toUpperCase() + kind.slice(1)}
                            </button>
                          ))}
                        </div>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <ConfirmDialog
        open={action?.kind === "close" || action?.kind === "recall"}
        title={action?.kind === "close" ? "Close contract" : "Recall contract"}
        message={
          action
            ? action.kind === "close"
              ? `Close ${action.contract.symbol} ${action.contract.series_code ?? action.contract.series ?? ""} (${action.contract.quantity_lent} shares)? This books its P&L once and returns the shares to "in demat".${actionError ? ` ${actionError}` : ""}`
              : `Recall ${action.contract.symbol} (${action.contract.quantity_lent} shares)? The contract ends early; no P&L is booked (correct the fee later if needed).${actionError ? ` ${actionError}` : ""}`
            : ""
        }
        confirmLabel={action?.kind === "close" ? "Close contract" : "Recall contract"}
        destructive={action?.kind === "recall"}
        busy={actionBusy}
        onCancel={() => setAction(null)}
        onConfirm={runAction}
      />
      {action?.kind === "roll" ? (
        <RollDialog
          contract={action.contract}
          onCancel={() => setAction(null)}
          onDone={() => {
            setAction(null);
            refetch();
          }}
        />
      ) : null}

      <Modal open={dialogOpen} title="Open lending contract" onClose={close}>
        {portfolio.loading ? (
          <TableSkeleton rows={3} cols={2} />
        ) : stocks.length === 0 ? (
          <EmptyState
            icon="📈"
            title="No holdings to lend"
            message="A lending contract must reference a portfolio holding. Add a stock on the Portfolio page first."
          />
        ) : (
          <ContractForm
            stocks={stocks}
            submitting={submitting}
            error={formError}
            onSubmit={handleCreate}
            onCancel={close}
          />
        )}
      </Modal>
    </div>
  );
}
