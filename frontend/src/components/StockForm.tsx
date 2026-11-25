import { useState, type FormEvent } from "react";
import type { Stock, StockCreate } from "@/types";
import { ApiError } from "@/api/client";
import { Spinner } from "./StateViews";

export interface StockFormValues {
  symbol: string;
  name: string;
  isin: string;
  quantity: number;
  avg_buy_price: number;
  is_slb_eligible: boolean;
  notes: string;
}

interface StockFormProps {
  /** Existing stock when editing; undefined when adding. */
  initial?: Stock;
  submitting: boolean;
  error: ApiError | null;
  onSubmit: (values: StockCreate) => void;
  onCancel: () => void;
}

export function StockForm({ initial, submitting, error, onSubmit, onCancel }: StockFormProps) {
  const isEdit = Boolean(initial);
  const [values, setValues] = useState<StockFormValues>({
    symbol: initial?.symbol ?? "",
    name: initial?.name ?? "",
    isin: initial?.isin ?? "",
    quantity: initial?.quantity ?? 0,
    avg_buy_price: initial?.avg_buy_price ?? 0,
    is_slb_eligible: initial?.is_slb_eligible ?? true,
    notes: initial?.notes ?? "",
  });
  const [localError, setLocalError] = useState<string | null>(null);

  const fieldError = (field: string) =>
    error?.fieldErrors.find((f) => f.field === field || f.field.endsWith(`.${field}`))?.message;

  function set<K extends keyof StockFormValues>(key: K, value: StockFormValues[K]) {
    setValues((v) => ({ ...v, [key]: value }));
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setLocalError(null);
    if (!values.symbol.trim()) {
      setLocalError("Symbol is required.");
      return;
    }
    if (values.quantity < 0 || values.avg_buy_price < 0) {
      setLocalError("Quantity and average price cannot be negative.");
      return;
    }
    onSubmit({
      symbol: values.symbol.trim().toUpperCase(),
      name: values.name.trim() || null,
      isin: values.isin.trim() || null,
      quantity: Number(values.quantity),
      avg_buy_price: Number(values.avg_buy_price),
      is_slb_eligible: values.is_slb_eligible,
      notes: values.notes.trim() || null,
    });
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="label" htmlFor="sf-symbol">
            Symbol *
          </label>
          <input
            id="sf-symbol"
            className="input uppercase"
            value={values.symbol}
            onChange={(e) => set("symbol", e.target.value)}
            disabled={isEdit /* symbol is the stable key; edit other fields */}
            placeholder="RELIANCE"
            autoFocus={!isEdit}
          />
          {fieldError("symbol") ? (
            <p className="mt-1 text-2xs text-neg">{fieldError("symbol")}</p>
          ) : null}
        </div>
        <div>
          <label className="label" htmlFor="sf-isin">
            ISIN
          </label>
          <input
            id="sf-isin"
            className="input"
            value={values.isin}
            onChange={(e) => set("isin", e.target.value)}
            placeholder="INE002A01018"
          />
        </div>
      </div>

      <div>
        <label className="label" htmlFor="sf-name">
          Company name
        </label>
        <input
          id="sf-name"
          className="input"
          value={values.name}
          onChange={(e) => set("name", e.target.value)}
          placeholder="Reliance Industries Ltd."
        />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="label" htmlFor="sf-qty">
            Quantity
          </label>
          <input
            id="sf-qty"
            type="number"
            min={0}
            className="input tnum"
            value={values.quantity}
            onChange={(e) => set("quantity", Number(e.target.value))}
          />
          {fieldError("quantity") ? (
            <p className="mt-1 text-2xs text-neg">{fieldError("quantity")}</p>
          ) : null}
        </div>
        <div>
          <label className="label" htmlFor="sf-price">
            Avg. buy price (₹)
          </label>
          <input
            id="sf-price"
            type="number"
            min={0}
            step="0.01"
            className="input tnum"
            value={values.avg_buy_price}
            onChange={(e) => set("avg_buy_price", Number(e.target.value))}
          />
          {fieldError("avg_buy_price") ? (
            <p className="mt-1 text-2xs text-neg">{fieldError("avg_buy_price")}</p>
          ) : null}
        </div>
      </div>

      <label className="flex items-center gap-2 text-sm text-slate-300">
        <input
          type="checkbox"
          checked={values.is_slb_eligible}
          onChange={(e) => set("is_slb_eligible", e.target.checked)}
          className="h-4 w-4 rounded border-line bg-surface-0"
        />
        SLB eligible
      </label>

      <div>
        <label className="label" htmlFor="sf-notes">
          Notes
        </label>
        <textarea
          id="sf-notes"
          className="input min-h-[64px]"
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
          {submitting ? <Spinner /> : isEdit ? "Save changes" : "Add stock"}
        </button>
      </div>
    </form>
  );
}
