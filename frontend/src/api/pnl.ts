import { http } from "./client";
import type { PnLBreakdown, PnLList, PnLRecord } from "@/types";

/**
 * P&L API. All money math is backend-owned (Decimal); the frontend only
 * displays the itemized figures the backend returns and never recomputes them.
 */
export const pnlApi = {
  list: (signal?: AbortSignal) => http.get<PnLList>("/api/pnl", signal),

  preview: (quantity_lent: number, lending_fee: number, tax_slab_pct?: number) =>
    http.post<PnLBreakdown>("/api/pnl/preview", {
      quantity_lent,
      lending_fee,
      tax_slab_pct: tax_slab_pct ?? null,
    }),

  recordForContract: (contractId: number) =>
    http.post<PnLRecord>(`/api/pnl/contracts/${contractId}`),
};
