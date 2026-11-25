import { useEffect, useState } from "react";
import { contractsApi } from "@/api/contracts";
import { toApiError } from "@/api/client";
import { useApiResource } from "@/hooks/useApiResource";
import { Modal } from "./Modal";
import { ErrorState, Spinner } from "./StateViews";
import { formatDate, formatINR, formatPercent, formatPrice, formatQty } from "@/utils/format";
import type { Contract, RolloverOption } from "@/types";

interface RollDialogProps {
  contract: Contract;
  onDone: () => void;
  onCancel: () => void;
}

/**
 * Roll a contract into a later Series B series (Phase 6.4). Options come from
 * GET /api/contracts/{id}/roll-options, priced at the bid; the best one within
 * SEBI's 365-day cap is pre-selected with its bid as the new fee. A roll past
 * the cap cannot be submitted.
 */
export function RollDialog({ contract, onDone, onCancel }: RollDialogProps) {
  const rec = useApiResource((signal) => contractsApi.rollOptions(contract.id, signal), [contract.id]);
  const [code, setCode] = useState<string | null>(null);
  const [fee, setFee] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const options = rec.data?.options ?? [];
  const selected: RolloverOption | undefined = options.find((o) => o.series_code === code);

  useEffect(() => {
    if (!rec.data || code !== null) return;
    const best = rec.data.best_option ?? rec.data.options[0];
    if (best) {
      setCode(best.series_code ?? null);
      setFee(best.best_bid != null ? String(best.best_bid) : "");
    }
  }, [rec.data, code]);

  const pastCap = selected ? selected.within_cap === false : false;
  const feeValue = Number(fee);
  const canRoll = Boolean(selected) && !pastCap && fee.trim() !== "" && feeValue >= 0 && !busy;

  async function submit() {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      await contractsApi.roll(contract.id, {
        expiry_date: selected.expiry_date,
        reverse_leg_date: selected.expiry_date,
        lending_fee: feeValue,
        series: selected.series,
        series_code: selected.series_code ?? null,
      });
      onDone();
    } catch (err) {
      setError(toApiError(err).userMessage);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      title={`Roll ${contract.symbol} ${contract.series_code ?? contract.series ?? ""}`}
      onClose={busy ? () => undefined : onCancel}
      footer={
        <>
          <button type="button" className="btn-ghost" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button type="button" className="btn-primary" onClick={submit} disabled={!canRoll}>
            {busy ? <Spinner label="Rolling…" /> : "Roll contract"}
          </button>
        </>
      }
    >
      {rec.loading ? (
        <Spinner label="Loading Series B options…" />
      ) : rec.error ? (
        <ErrorState error={rec.error} onRetry={rec.refetch} />
      ) : (
        <div className="space-y-3 text-sm">
          <p className="text-2xs text-slate-400">
            <span className="font-semibold text-slate-200">{rec.data?.days_used_label}</span>
            {rec.data?.origin_date ? ` (since ${formatDate(rec.data.origin_date)})` : ""}. The old contract
            becomes ROLLED OVER and its fee is booked once.
          </p>
          {options.length === 0 ? (
            <p className="text-2xs text-warn">No later Series B series has been captured yet.</p>
          ) : (
            <fieldset className="space-y-1">
              <legend className="label">Next Series B contract</legend>
              {options.map((o) => (
                <label key={o.series_code ?? o.expiry_date} className="flex items-start gap-2 text-2xs">
                  <input
                    type="radio"
                    name="roll-option"
                    checked={o.series_code === code}
                    onChange={() => {
                      setCode(o.series_code ?? null);
                      setFee(o.best_bid != null ? String(o.best_bid) : "");
                    }}
                  />
                  <span className={o.within_cap === false ? "text-slate-500" : "text-slate-200"}>
                    {o.series_code} · settles {formatDate(o.expiry_date)} · bid{" "}
                    {o.best_bid != null ? `₹${formatPrice(o.best_bid)} (${formatQty(o.best_bid_qty ?? 0)})` : "none"}
                    {o.net_yield_pa != null
                      ? ` · nets ${formatPercent(o.net_yield_pa)} (${formatINR(o.net_fee ?? 0)} on ${formatQty(o.fillable ?? 0)})`
                      : ""}
                    {` · ${o.chain_days} of 365 days`}
                    {o.within_cap === false ? " · beyond the cap" : ""}
                  </span>
                </label>
              ))}
            </fieldset>
          )}
          {pastCap ? (
            <p role="alert" className="text-2xs text-neg">
              Blocked: this series settles past the 365-day cap from the original contract date, which SEBI
              does not allow.
            </p>
          ) : null}
          <div>
            <label className="label" htmlFor="roll-fee">
              New fee per share (₹)
            </label>
            <input
              id="roll-fee"
              type="number"
              min={0}
              step={0.01}
              className="input"
              value={fee}
              onChange={(e) => setFee(e.target.value)}
            />
            <p className="mt-1 text-2xs text-slate-500">Pre-filled with the live bid; enter the fee you actually got.</p>
          </div>
          {error ? <p className="text-2xs text-neg">{error}</p> : null}
        </div>
      )}
    </Modal>
  );
}
