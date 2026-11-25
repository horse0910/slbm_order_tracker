import { useState, type FormEvent } from "react";
import type { ContractCreate, Stock } from "@/types";
import { ApiError } from "@/api/client";
import { Spinner } from "./StateViews";

interface ContractFormValues {
  stock_id: string; // select value is a string; parsed on submit
  series: string;
  trade_date: string;
  expiry_date: string;
  reverse_leg_date: string;
  quantity_lent: string;
  lending_fee: string;
  notes: string;
}

interface ContractFormProps {
  /** Portfolio holdings a contract can be opened against (must be non-empty). */
  stocks: Stock[];
  submitting: boolean;
  error: ApiError | null;
  onSubmit: (values: ContractCreate) => void;
  onCancel: () => void;
}

const EMPTY: ContractFormValues = {
  stock_id: "",
  series: "",
  trade_date: "",
  expiry_date: "",
  reverse_leg_date: "",
  quantity_lent: "",
  lending_fee: "",
  notes: "",
};

/**
 * Form to open a lending contract. Deliberately performs NO financial
 * calculation (no income preview): quantity × fee and all lifecycle state are
 * computed authoritatively by the backend and returned on creation. The form
 * only does light input validation to give fast feedback before the request.
 */
export function ContractForm({ stocks, submitting, error, onSubmit, onCancel }: ContractFormProps) {
  const [values, setValues] = useState<ContractFormValues>({
    ...EMPTY,
    stock_id: stocks.length === 1 ? String(stocks[0].id) : "",
  });
  const [localError, setLocalError] = useState<string | null>(null);

  const fieldError = (field: string) =>
    error?.fieldErrors.find((f) => f.field === field || f.field.endsWith(`.${field}`))?.message;

  function set<K extends keyof ContractFormValues>(key: K, value: ContractFormValues[K]) {
    setValues((v) => ({ ...v, [key]: value }));
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setLocalError(null);

    if (!values.stock_id) {
      setLocalError("Select the holding being lent.");
      return;
    }
    if (!values.expiry_date) {
      setLocalError("Expiry date is required.");
      return;
    }
    const qty = Number(values.quantity_lent);
    const fee = Number(values.lending_fee);
    if (!Number.isFinite(qty) || qty <= 0) {
      setLocalError("Quantity lent must be a positive number of shares.");
      return;
    }
    if (!Number.isFinite(fee) || fee < 0) {
      setLocalError("Lending fee per share cannot be negative.");
      return;
    }
    if (values.trade_date && values.expiry_date < values.trade_date) {
      setLocalError("Expiry date cannot be before the trade date.");
      return;
    }

    onSubmit({
      stock_id: Number(values.stock_id),
      series: values.series.trim() || null,
      trade_date: values.trade_date || null,
      expiry_date: values.expiry_date,
      reverse_leg_date: values.reverse_leg_date || null,
      quantity_lent: qty,
      lending_fee: fee,
      notes: values.notes.trim() || null,
    });
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <div>
        <label className="label" htmlFor="cf-stock">
          Holding *
        </label>
        <select
          id="cf-stock"
          className="input"
          value={values.stock_id}
          onChange={(e) => set("stock_id", e.target.value)}
          autoFocus
        >
          <option value="">Select a holding…</option>
          {stocks.map((s) => (
            <option key={s.id} value={s.id}>
              {s.symbol}
              {s.name ? ` — ${s.name}` : ""} ({s.quantity} held)
            </option>
          ))}
        </select>
        {fieldError("stock_id") ? (
          <p className="mt-1 text-2xs text-neg">{fieldError("stock_id")}</p>
        ) : null}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="label" htmlFor="cf-qty">
            Quantity lent *
          </label>
          <input
            id="cf-qty"
            type="number"
            min={1}
            step="1"
            className="input tnum"
            value={values.quantity_lent}
            onChange={(e) => set("quantity_lent", e.target.value)}
            placeholder="100"
          />
          {fieldError("quantity_lent") ? (
            <p className="mt-1 text-2xs text-neg">{fieldError("quantity_lent")}</p>
          ) : null}
        </div>
        <div>
          <label className="label" htmlFor="cf-fee">
            Lending fee / share (₹) *
          </label>
          <input
            id="cf-fee"
            type="number"
            min={0}
            step="0.001"
            className="input tnum"
            value={values.lending_fee}
            onChange={(e) => set("lending_fee", e.target.value)}
            placeholder="5.25"
          />
          {fieldError("lending_fee") ? (
            <p className="mt-1 text-2xs text-neg">{fieldError("lending_fee")}</p>
          ) : null}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="label" htmlFor="cf-trade">
            Trade date
          </label>
          <input
            id="cf-trade"
            type="date"
            className="input tnum"
            value={values.trade_date}
            onChange={(e) => set("trade_date", e.target.value)}
          />
          <p className="mt-1 text-2xs text-slate-500">Defaults to today (IST) if left blank.</p>
        </div>
        <div>
          <label className="label" htmlFor="cf-expiry">
            Expiry date *
          </label>
          <input
            id="cf-expiry"
            type="date"
            className="input tnum"
            value={values.expiry_date}
            onChange={(e) => set("expiry_date", e.target.value)}
          />
          {fieldError("expiry_date") ? (
            <p className="mt-1 text-2xs text-neg">{fieldError("expiry_date")}</p>
          ) : null}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="label" htmlFor="cf-series">
            Series
          </label>
          <input
            id="cf-series"
            className="input"
            value={values.series}
            onChange={(e) => set("series", e.target.value)}
            placeholder="Oct-2026"
            maxLength={10}
          />
        </div>
        <div>
          <label className="label" htmlFor="cf-reverse">
            Reverse-leg date
          </label>
          <input
            id="cf-reverse"
            type="date"
            className="input tnum"
            value={values.reverse_leg_date}
            onChange={(e) => set("reverse_leg_date", e.target.value)}
          />
        </div>
      </div>

      <div>
        <label className="label" htmlFor="cf-notes">
          Notes
        </label>
        <textarea
          id="cf-notes"
          className="input min-h-[56px]"
          value={values.notes}
          onChange={(e) => set("notes", e.target.value)}
        />
      </div>

      {localError ? <p className="text-2xs text-neg">{localError}</p> : null}
      {error && error.fieldErrors.length === 0 ? (
        <p className="text-2xs text-neg">{error.userMessage}</p>
      ) : null}

      <div className="flex justify-end gap-2 pt-2">
        <button type="button" className="btn-ghost" onClick={onCancel} disabled={submitting}>
          Cancel
        </button>
        <button type="submit" className="btn-primary" disabled={submitting}>
          {submitting ? <Spinner /> : "Open contract"}
        </button>
      </div>
    </form>
  );
}
