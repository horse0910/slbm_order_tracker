import { useEffect, useMemo, useState, type FormEvent } from "react";
import { alertsApi } from "@/api/alerts";
import { portfolioApi } from "@/api/portfolio";
import { ApiError, toApiError } from "@/api/client";
import { useApiResource } from "@/hooks/useApiResource";
import { useLiveAlerts } from "@/context/LiveAlertsProvider";
import { Card, SectionHeader } from "@/components/Card";
import { EmptyState, ErrorState, Spinner, TableSkeleton } from "@/components/StateViews";
import { Modal } from "@/components/Modal";
import { formatDateTime } from "@/utils/format";
import type { Alert, AlertCreate, AlertDeliveryLog, AlertType, NotificationDelivery, Stock } from "@/types";

const CHANNEL_LABELS: Record<string, string> = {
  in_app: "In-app",
  email: "Email",
};

/** Delivered/failed indicator for a delivery-log row (glyph + text, not color-only). */
function DeliveredPill({ delivered }: { delivered: boolean }) {
  return delivered ? (
    <span className="pill bg-pos/15 text-pos" title="Delivered">✓ Sent</span>
  ) : (
    <span className="pill bg-surface-3 text-slate-400" title="Not delivered">✗ Skipped</span>
  );
}

/** Type metadata: label + what the threshold means (units) for the form/UI. */
const ALERT_TYPE_META: Record<AlertType, { label: string; thresholdLabel: string | null; hint: string }> = {
  YIELD_THRESHOLD: {
    label: "Gross yield threshold",
    thresholdLabel: "Min gross yield %",
    hint: "Fires when a symbol's GROSS annualized yield (NSE's figure, before Zerodha's charges) is at or above the threshold.",
  },
  LENDING_OPPORTUNITY: {
    label: "Lending opportunity",
    thresholdLabel: "Min net yield %",
    hint: "Fires when a holding has an actionable option at the live Series B bid: net yield after charges at or above the floor, a usable fill, no corporate action, fresh quote. The message carries the Zerodha ticket line.",
  },
  ABOVE_AVERAGE: {
    label: "Above average",
    thresholdLabel: "Margin % above average",
    hint: "Fires when a symbol's latest yield exceeds its own trailing average by the margin. Needs a scoped holding.",
  },
  TIGHT_SPREAD: {
    label: "Tight spread",
    thresholdLabel: "Max spread %",
    hint: "Fires when the bid/ask spread is at or below the threshold percent.",
  },
  ROLLOVER: {
    label: "Rollover",
    thresholdLabel: "Days before reverse leg",
    hint: "Fires this many days (default rollover_warn_days, 5) before an open contract's reverse leg, with the next Series B bid in the message.",
  },
  EXPIRY_REMINDER: {
    label: "Expiry reminder",
    thresholdLabel: "Days before expiry",
    hint: "Fires when an open contract is within this many days of expiry.",
  },
};

const ALERT_TYPES = Object.keys(ALERT_TYPE_META) as AlertType[];

function TriggerPill({ alert }: { alert: Alert }) {
  if (!alert.is_active) {
    return <span className="pill bg-surface-3 text-slate-500" title="Rule disabled">Paused</span>;
  }
  return alert.is_triggered ? (
    <span className="pill bg-warn/20 text-warn" title="Condition currently met">⚠ Triggered</span>
  ) : (
    <span className="pill bg-pos/15 text-pos" title="Armed, condition not met">● Armed</span>
  );
}

function AlertForm({
  stocks,
  submitting,
  error,
  onSubmit,
  onCancel,
}: {
  stocks: Stock[];
  submitting: boolean;
  error: ApiError | null;
  onSubmit: (values: AlertCreate) => void;
  onCancel: () => void;
}) {
  const [type, setType] = useState<AlertType>("YIELD_THRESHOLD");
  const [threshold, setThreshold] = useState("");
  const [stockId, setStockId] = useState("");
  const meta = ALERT_TYPE_META[type];

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    onSubmit({
      alert_type: type,
      threshold: threshold.trim() === "" ? null : Number(threshold),
      stock_id: stockId === "" ? null : Number(stockId),
      is_active: true,
    });
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <div>
        <label className="label" htmlFor="af-type">
          Alert type
        </label>
        <select
          id="af-type"
          className="input"
          value={type}
          onChange={(e) => setType(e.target.value as AlertType)}
        >
          {ALERT_TYPES.map((t) => (
            <option key={t} value={t}>
              {ALERT_TYPE_META[t].label}
            </option>
          ))}
        </select>
        <p className="mt-1 text-2xs text-slate-500">{meta.hint}</p>
      </div>

      {meta.thresholdLabel ? (
        <div>
          <label className="label" htmlFor="af-threshold">
            {meta.thresholdLabel}
          </label>
          <input
            id="af-threshold"
            type="number"
            step="0.01"
            className="input tnum"
            value={threshold}
            onChange={(e) => setThreshold(e.target.value)}
            placeholder="Leave blank for the configured default"
          />
        </div>
      ) : null}

      <div>
        <label className="label" htmlFor="af-stock">
          Scope
        </label>
        <select
          id="af-stock"
          className="input"
          value={stockId}
          onChange={(e) => setStockId(e.target.value)}
        >
          <option value="">Portfolio-wide (all symbols)</option>
          {stocks.map((s) => (
            <option key={s.id} value={s.id}>
              {s.symbol}
            </option>
          ))}
        </select>
      </div>

      {error && error.fieldErrors.length === 0 ? (
        <p className="text-2xs text-neg">{error.userMessage}</p>
      ) : null}

      <div className="flex justify-end gap-2 pt-2">
        <button type="button" className="btn-ghost" onClick={onCancel} disabled={submitting}>
          Cancel
        </button>
        <button type="submit" className="btn-primary" disabled={submitting}>
          {submitting ? <Spinner /> : "Create rule"}
        </button>
      </div>
    </form>
  );
}

export function AlertsPage() {
  const { data, error, loading, refreshing, refetch } = useApiResource<Alert[]>(
    (signal) => alertsApi.list(false, signal),
    [],
  );
  const portfolio = useApiResource<Stock[]>((signal) => portfolioApi.list(signal), []);
  const stocks = useMemo(() => portfolio.data ?? [], [portfolio.data]);
  const deliveryLog = useApiResource<AlertDeliveryLog>(
    (signal) => alertsApi.deliveries(signal),
    [],
  );

  // Real-time push (Phase 4): when the backend broadcasts a fired alert, refetch
  // the authoritative rule list from REST — never recompute state client-side.
  // The delivery-audit log is refetched on the same signal so it reflects what
  // was just delivered.
  const { status: liveStatus, eventCount } = useLiveAlerts();
  const refetchDeliveries = deliveryLog.refetch;
  useEffect(() => {
    if (eventCount > 0) {
      refetch();
      refetchDeliveries();
    }
  }, [eventCount, refetch, refetchDeliveries]);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<ApiError | null>(null);
  const [evaluating, setEvaluating] = useState(false);
  const [runSummary, setRunSummary] = useState<string | null>(null);

  const alerts = data ?? [];
  const deliveries = deliveryLog.data?.deliveries ?? [];
  const emailActive = deliveryLog.data?.email_channel_active ?? false;

  async function handleCreate(payload: AlertCreate) {
    setSubmitting(true);
    setFormError(null);
    try {
      await alertsApi.create(payload);
      setDialogOpen(false);
      refetch();
    } catch (err) {
      setFormError(toApiError(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleToggle(alert: Alert) {
    await alertsApi.setActive(alert.id, !alert.is_active);
    refetch();
  }

  async function handleDelete(alert: Alert) {
    await alertsApi.remove(alert.id);
    refetch();
  }

  async function handleEvaluate() {
    setEvaluating(true);
    setRunSummary(null);
    try {
      const run = await alertsApi.evaluate();
      setRunSummary(`Evaluated ${run.evaluated} rule(s); ${run.fired} newly triggered.`);
      refetch();
      refetchDeliveries();
    } catch (err) {
      setRunSummary(toApiError(err).userMessage);
    } finally {
      setEvaluating(false);
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-slate-500">
          Configure alert rules. Conditions are evaluated and delivered by the backend — rules here
          only define what to watch for.
        </p>
        <div className="flex items-center gap-2">
          <span
            className={`pill ${emailActive ? "bg-pos/15 text-pos" : "bg-surface-3 text-slate-500"}`}
            title={
              emailActive
                ? "Email delivery is configured (SMTP). Fired alerts are emailed."
                : "Email delivery is off. Configure SMTP_* env vars to enable it; in-app alerts still work."
            }
          >
            {emailActive ? "✉ Email on" : "✉ Email off"}
          </span>
          <span
            className={`pill ${
              liveStatus === "open" ? "bg-pos/15 text-pos" : "bg-surface-3 text-slate-500"
            }`}
            title={
              liveStatus === "open"
                ? "Live updates connected — fired alerts arrive in real time."
                : "Live updates offline — showing the last fetched data."
            }
          >
            {liveStatus === "open" ? "🟢 Live" : "⚪ Offline"}
          </span>
          <button type="button" className="btn-ghost" onClick={handleEvaluate} disabled={evaluating}>
            {evaluating ? <Spinner label="Evaluating" /> : "Evaluate now"}
          </button>
          <button type="button" className="btn-primary" onClick={() => setDialogOpen(true)}>
            + New rule
          </button>
        </div>
      </div>

      {runSummary ? (
        <div className="rounded-md border border-line bg-surface-2/50 px-3 py-2 text-2xs text-slate-300">
          {runSummary}
        </div>
      ) : null}

      <Card>
        <SectionHeader
          title="Alert rules"
          subtitle={data ? `${alerts.length} rule${alerts.length === 1 ? "" : "s"}` : undefined}
          right={
            <button type="button" className="btn-ghost" onClick={refetch} disabled={refreshing}>
              Refresh
            </button>
          }
        />

        {loading ? (
          <TableSkeleton rows={4} cols={5} />
        ) : error ? (
          <div className="p-4">
            <ErrorState error={error} onRetry={refetch} />
          </div>
        ) : alerts.length === 0 ? (
          <EmptyState
            icon="🔔"
            title="No alert rules yet"
            message="Create a rule to watch for high yields, tight spreads, above-average opportunities, upcoming expiries or rollover candidates."
            action={
              <button type="button" className="btn-primary" onClick={() => setDialogOpen(true)}>
                New rule
              </button>
            }
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr className="border-b border-line">
                  <th className="th">Type</th>
                  <th className="th text-right">Threshold</th>
                  <th className="th">Scope</th>
                  <th className="th">State</th>
                  <th className="th">Last triggered</th>
                  <th className="th text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {alerts.map((a) => {
                  const meta = ALERT_TYPE_META[a.alert_type];
                  const scopeStock = stocks.find((s) => s.id === a.stock_id);
                  return (
                    <tr key={a.id} className="border-b border-line/50 hover:bg-surface-2/40 align-top">
                      <td className="td font-medium">
                        {meta?.label ?? a.alert_type}
                        {a.trigger_message ? (
                          <p className="mt-0.5 max-w-[320px] text-2xs font-normal text-slate-500">
                            {a.trigger_message}
                          </p>
                        ) : null}
                      </td>
                      <td className="td text-right tnum">
                        {a.threshold ?? <span className="text-slate-500">default</span>}
                      </td>
                      <td className="td text-slate-300">
                        {a.stock_id == null ? "All symbols" : (scopeStock?.symbol ?? `#${a.stock_id}`)}
                      </td>
                      <td className="td">
                        <TriggerPill alert={a} />
                      </td>
                      <td className="td text-2xs text-slate-500">
                        {formatDateTime(a.last_triggered_at)}
                      </td>
                      <td className="td text-right">
                        <div className="flex justify-end gap-1">
                          <button
                            type="button"
                            className="rounded px-2 py-1 text-2xs text-slate-300 hover:bg-surface-3"
                            onClick={() => handleToggle(a)}
                          >
                            {a.is_active ? "Pause" : "Resume"}
                          </button>
                          <button
                            type="button"
                            className="rounded px-2 py-1 text-2xs text-neg hover:bg-neg/15"
                            onClick={() => handleDelete(a)}
                          >
                            Delete
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card>
        <SectionHeader
          title="Delivery log"
          subtitle={
            deliveryLog.data
              ? `${deliveries.length} recent ${deliveries.length === 1 ? "attempt" : "attempts"}`
              : undefined
          }
          right={
            <button
              type="button"
              className="btn-ghost"
              onClick={refetchDeliveries}
              disabled={deliveryLog.refreshing}
            >
              Refresh
            </button>
          }
        />
        <p className="px-4 pb-2 text-2xs text-slate-500">
          Audit of notification-delivery attempts (best-effort, newest first). In-app delivery is
          always on; email is sent only when SMTP is configured via environment variables.
        </p>

        {deliveryLog.loading ? (
          <TableSkeleton rows={3} cols={4} />
        ) : deliveryLog.error ? (
          <div className="p-4">
            <ErrorState error={deliveryLog.error} onRetry={refetchDeliveries} />
          </div>
        ) : deliveries.length === 0 ? (
          <EmptyState
            icon="✉"
            title="No deliveries yet"
            message="When a rule triggers, each delivery attempt (in-app and, if configured, email) is recorded here."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr className="border-b border-line">
                  <th className="th">Channel</th>
                  <th className="th">Result</th>
                  <th className="th">Detail</th>
                  <th className="th">Time</th>
                </tr>
              </thead>
              <tbody>
                {deliveries.map((d: NotificationDelivery) => (
                  <tr key={d.id} className="border-b border-line/50 hover:bg-surface-2/40 align-top">
                    <td className="td font-medium">{CHANNEL_LABELS[d.channel] ?? d.channel}</td>
                    <td className="td">
                      <DeliveredPill delivered={d.delivered} />
                    </td>
                    <td className="td max-w-[360px] text-2xs text-slate-400">{d.detail ?? "—"}</td>
                    <td className="td text-2xs text-slate-500">{formatDateTime(d.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Modal
        open={dialogOpen}
        title="New alert rule"
        onClose={() => {
          setDialogOpen(false);
          setFormError(null);
        }}
      >
        <AlertForm
          stocks={stocks}
          submitting={submitting}
          error={formError}
          onSubmit={handleCreate}
          onCancel={() => {
            setDialogOpen(false);
            setFormError(null);
          }}
        />
      </Modal>
    </div>
  );
}
