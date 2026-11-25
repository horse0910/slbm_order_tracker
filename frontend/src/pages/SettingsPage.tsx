import { useEffect, useState, type FormEvent } from "react";
import { settingsApi } from "@/api/settings";
import { useApiResource } from "@/hooks/useApiResource";
import { ApiError, toApiError } from "@/api/client";
import { Card, SectionHeader } from "@/components/Card";
import { ErrorState, Spinner, TableSkeleton } from "@/components/StateViews";
import { TestNotification } from "@/components/TestNotification";
import type { BusinessSettings, BusinessSettingsUpdate } from "@/types";

type BoolKey = "notifications_enabled" | "macos_notifications_enabled";
type NumericKey = Exclude<keyof BusinessSettings, BoolKey>;

interface NumberFieldProps {
  id: string;
  label: string;
  hint: string;
  value: number;
  min?: number;
  max?: number;
  step?: number | string;
  onChange: (value: number) => void;
  error?: string;
}

function NumberField({ id, label, hint, value, min, max, step, onChange, error }: NumberFieldProps) {
  return (
    <div className="border-b border-line/50 px-4 py-3 last:border-b-0">
      <div className="flex items-center justify-between gap-4">
        <label className="label mb-0" htmlFor={id}>
          {label}
        </label>
        <input
          id={id}
          type="number"
          className="input tnum w-32 text-right"
          value={Number.isFinite(value) ? value : ""}
          min={min}
          max={max}
          step={step ?? "any"}
          onChange={(e) => onChange(e.target.value === "" ? NaN : Number(e.target.value))}
        />
      </div>
      <p className="mt-1 text-2xs text-slate-500">{hint}</p>
      {error ? <p className="mt-1 text-2xs text-neg">{error}</p> : null}
    </div>
  );
}

export function SettingsPage() {
  const { data, error, loading, refetch } = useApiResource<BusinessSettings>(
    (signal) => settingsApi.get(signal),
    [],
  );

  const [form, setForm] = useState<BusinessSettings | null>(null);
  const [baseline, setBaseline] = useState<BusinessSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<ApiError | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  // Seed (and re-seed) the editable form whenever the effective config loads.
  useEffect(() => {
    if (data) {
      setForm(data);
      setBaseline(data);
    }
  }, [data]);

  if (error && !data) {
    return (
      <Card className="panel-pad">
        <ErrorState error={error} onRetry={refetch} />
      </Card>
    );
  }

  if (loading || !form || !baseline) {
    return (
      <div className="space-y-5">
        <Card>
          <SectionHeader title="Contracts" />
          <TableSkeleton rows={1} cols={2} />
        </Card>
        <Card>
          <SectionHeader title="Alerts" />
          <TableSkeleton rows={4} cols={2} />
        </Card>
        <Card>
          <SectionHeader title="P&L" />
          <TableSkeleton rows={4} cols={2} />
        </Card>
      </div>
    );
  }

  const dirty = JSON.stringify(form) !== JSON.stringify(baseline);
  const invalid = Object.values(form).some((v) => typeof v === "number" && !Number.isFinite(v));

  const setNum = (key: NumericKey, value: number) => {
    setForm((f) => (f ? { ...f, [key]: value } : f));
    setStatus(null);
  };

  const fieldError = (field: string) =>
    saveError?.fieldErrors.find((f) => f.field === field || f.field.endsWith(`.${field}`))?.message;

  async function handleSave(e: FormEvent) {
    e.preventDefault();
    if (!form || !baseline) return;
    // Send only the keys that actually changed. Untouched keys are omitted so
    // they keep following their env/persisted fallback instead of being pinned
    // as overrides — this is the backend's documented partial-update contract.
    const changed: BusinessSettingsUpdate = {};
    for (const key of Object.keys(form) as (keyof BusinessSettings)[]) {
      if (baseline[key] !== form[key]) {
        (changed as Record<string, unknown>)[key] = form[key];
      }
    }
    setSaving(true);
    setSaveError(null);
    setStatus(null);
    try {
      const next = await settingsApi.update(changed);
      setForm(next);
      setBaseline(next);
      setStatus("Settings saved — they take effect immediately.");
    } catch (err) {
      setSaveError(toApiError(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleReset() {
    setSaving(true);
    setSaveError(null);
    setStatus(null);
    try {
      const next = await settingsApi.reset();
      setForm(next);
      setBaseline(next);
      setStatus("Reset to environment defaults.");
    } catch (err) {
      setSaveError(toApiError(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={handleSave} noValidate className="space-y-5">
      <p className="text-xs text-slate-500">
        Editable business configuration driving the contract, alert and P&amp;L engines. Changes are
        saved to the app and apply immediately, layered over the environment/.env defaults.
        Credentials (e.g. SMTP) are never stored here or exposed to this view.
      </p>

      <Card>
        <SectionHeader title="Contracts" />
        <NumberField
          id="set-expiring_soon_days"
          label="Expiring-soon window (days)"
          hint="Contracts within this many days of expiry are flagged expiring soon. 1–60."
          value={form.expiring_soon_days}
          min={1}
          max={60}
          step={1}
          onChange={(v) => setNum("expiring_soon_days", v)}
          error={fieldError("expiring_soon_days")}
        />
        <NumberField
          id="set-rollover_warn_days"
          label="Rollover warning (days before reverse leg)"
          hint="The ROLLOVER alert fires this many days before a contract's reverse leg, with the next Series B bid. 1–60."
          value={form.rollover_warn_days}
          min={1}
          max={60}
          step={1}
          onChange={(v) => setNum("rollover_warn_days", v)}
          error={fieldError("rollover_warn_days")}
        />
      </Card>

      <Card>
        <SectionHeader
          title="Opportunities"
          subtitle="When a Series B bid on your lendable shares counts as actionable (net of Zerodha's charges)."
        />
        <NumberField
          id="set-min_net_yield_pct"
          label="Net yield floor (%)"
          hint="Net annualised yield at the bid, after processing, GST and the flat DP charge. 0–100."
          value={form.min_net_yield_pct}
          min={0}
          max={100}
          step={0.1}
          onChange={(v) => setNum("min_net_yield_pct", v)}
          error={fieldError("min_net_yield_pct")}
        />
        <NumberField
          id="set-min_fill_qty"
          label="Minimum fill (shares)"
          hint="Smallest fillable quantity (lendable capped by the bid quantity) worth acting on."
          value={form.min_fill_qty}
          min={1}
          step={1}
          onChange={(v) => setNum("min_fill_qty", v)}
          error={fieldError("min_fill_qty")}
        />
        <NumberField
          id="set-min_net_fee_inr"
          label="Minimum net fee (₹)"
          hint="Skip options whose net fee is below this; 0 = off. Guards against high yields on tiny fees near expiry."
          value={form.min_net_fee_inr}
          min={0}
          step={10}
          onChange={(v) => setNum("min_net_fee_inr", v)}
          error={fieldError("min_net_fee_inr")}
        />
        <NumberField
          id="set-tracked_expiries"
          label="Expiries shown per stock"
          hint="How many upcoming Series B expiries the opportunity board and Expiries view show. 1–6."
          value={form.tracked_expiries}
          min={1}
          max={6}
          step={1}
          onChange={(v) => setNum("tracked_expiries", v)}
          error={fieldError("tracked_expiries")}
        />
      </Card>

      <Card>
        <SectionHeader title="Alerts" />
        <NumberField
          id="set-default_alert_yield_threshold"
          label="Default yield threshold (%)"
          hint="Minimum GROSS annualized yield (before charges) to trigger a YIELD_THRESHOLD alert (≥)."
          value={form.default_alert_yield_threshold}
          min={0}
          step={0.1}
          onChange={(v) => setNum("default_alert_yield_threshold", v)}
          error={fieldError("default_alert_yield_threshold")}
        />
        <NumberField
          id="set-tight_spread_threshold_pct"
          label="Tight-spread threshold (%)"
          hint="Bid/ask spread at or below this fires a TIGHT_SPREAD alert (≤)."
          value={form.tight_spread_threshold_pct}
          min={0}
          step={0.1}
          onChange={(v) => setNum("tight_spread_threshold_pct", v)}
          error={fieldError("tight_spread_threshold_pct")}
        />
        <NumberField
          id="set-above_average_yield_margin_pct"
          label="Above-average margin (%)"
          hint="How far above a symbol's trailing average yield counts as above-average."
          value={form.above_average_yield_margin_pct}
          min={0}
          step={1}
          onChange={(v) => setNum("above_average_yield_margin_pct", v)}
          error={fieldError("above_average_yield_margin_pct")}
        />
        <NumberField
          id="set-alert_cooldown_minutes"
          label="Alert cooldown (minutes)"
          hint="After a rule fires, a re-crossing within this time sends nothing new. 0 = off; up to 1440."
          value={form.alert_cooldown_minutes}
          min={0}
          max={1440}
          step={5}
          onChange={(v) => setNum("alert_cooldown_minutes", v)}
          error={fieldError("alert_cooldown_minutes")}
        />
        <label className="flex items-center justify-between gap-4 px-4 py-3">
          <span>
            <span className="block text-sm text-slate-200">Notifications</span>
            <span className="mt-0.5 block text-2xs text-slate-500">
              Master switch for alert delivery (in-app). Evaluation is unaffected.
            </span>
          </span>
          <input
            type="checkbox"
            checked={form.notifications_enabled}
            onChange={(e) => {
              const checked = e.target.checked;
              setForm((f) => (f ? { ...f, notifications_enabled: checked } : f));
              setStatus(null);
            }}
            className="h-4 w-4 rounded border-line bg-surface-0"
            aria-label="Notifications enabled"
          />
        </label>
      </Card>

      <Card>
        <SectionHeader
          title="Notifications"
          subtitle="macOS on this Mac; WhatsApp to your phone when TWILIO_* and WHATSAPP_* are set in .env."
        />
        <label className="flex items-center justify-between gap-4 px-4 py-3">
          <span>
            <span className="block text-sm text-slate-200">macOS notifications</span>
            <span className="mt-0.5 block text-2xs text-slate-500">
              Show alerts in Notification Centre when the app runs on this Mac.
            </span>
          </span>
          <input
            type="checkbox"
            checked={form.macos_notifications_enabled}
            onChange={(e) => {
              const checked = e.target.checked;
              setForm((f) => (f ? { ...f, macos_notifications_enabled: checked } : f));
              setStatus(null);
            }}
            className="h-4 w-4 rounded border-line bg-surface-0"
            aria-label="macOS notifications"
          />
        </label>
        <TestNotification />
      </Card>

      <Card>
        <SectionHeader
          title="Live board"
          subtitle="How often the board sweeps every holding during market hours."
        />
        <NumberField
          id="set-live_refresh_seconds"
          label="Live board refresh (seconds)"
          hint="Every sweep asks NSE once per holding, 2 s apart. 30–3600; 300 halves the load."
          value={form.live_refresh_seconds}
          min={30}
          max={3600}
          step={30}
          onChange={(v) => setNum("live_refresh_seconds", v)}
          error={fieldError("live_refresh_seconds")}
        />
      </Card>

      <Card>
        <SectionHeader title="P&L" subtitle="Rates applied by the backend P&L engine." />
        <NumberField
          id="set-broker_processing_fee_pct"
          label="Broker processing fee (%)"
          hint="Percent of the gross lending fee taken as processing charge. 0–100."
          value={form.broker_processing_fee_pct}
          min={0}
          max={100}
          step={0.1}
          onChange={(v) => setNum("broker_processing_fee_pct", v)}
          error={fieldError("broker_processing_fee_pct")}
        />
        <NumberField
          id="set-gst_pct"
          label="GST (%)"
          hint="Applied on the processing charge. 0–100."
          value={form.gst_pct}
          min={0}
          max={100}
          step={0.1}
          onChange={(v) => setNum("gst_pct", v)}
          error={fieldError("gst_pct")}
        />
        <NumberField
          id="set-dp_charge_flat_inr"
          label="DP charge per scrip (₹, before GST)"
          hint="Flat depository charge per contract; GST is added on top (₹13 + 18% = ₹15.34). 0–1000."
          value={form.dp_charge_flat_inr}
          min={0}
          max={1000}
          step={0.01}
          onChange={(v) => setNum("dp_charge_flat_inr", v)}
          error={fieldError("dp_charge_flat_inr")}
        />
        <NumberField
          id="set-default_tax_slab_pct"
          label="Tax slab — estimate (%)"
          hint="Default slab for the clearly-labeled tax ESTIMATE only — not an actual liability. 0–100."
          value={form.default_tax_slab_pct}
          min={0}
          max={100}
          step={1}
          onChange={(v) => setNum("default_tax_slab_pct", v)}
          error={fieldError("default_tax_slab_pct")}
        />
      </Card>

      {status ? <p className="text-2xs text-pos">{status}</p> : null}
      {saveError && saveError.fieldErrors.length === 0 ? (
        <p className="text-2xs text-neg">{saveError.userMessage}</p>
      ) : null}

      <div className="flex justify-end gap-2">
        <button type="button" className="btn-ghost" onClick={handleReset} disabled={saving}>
          Reset to defaults
        </button>
        <button type="submit" className="btn-primary" disabled={saving || !dirty || invalid}>
          {saving ? <Spinner /> : "Save changes"}
        </button>
      </div>
    </form>
  );
}
