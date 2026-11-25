import { http } from "./client";
import type { Contract, ContractCreate, ContractRollPayload, ContractStatus, RolloverRecommendation } from "@/types";

/**
 * Lending-contract API.
 *
 * All lifecycle state (effective_status, days_remaining, income, rollover
 * candidacy) is computed by the backend — this module just transports it. The
 * optional `effectiveStatus` filter maps to the backend `effective_status`
 * query param (server-side derivation), so the UI never filters on recomputed
 * dates.
 */
export const contractsApi = {
  list: (effectiveStatus?: ContractStatus, signal?: AbortSignal) => {
    const qs = effectiveStatus
      ? `?effective_status=${encodeURIComponent(effectiveStatus)}`
      : "";
    return http.get<Contract[]>(`/api/contracts${qs}`, signal);
  },

  get: (id: number, signal?: AbortSignal) =>
    http.get<Contract>(`/api/contracts/${id}`, signal),

  create: (payload: ContractCreate) => http.post<Contract>("/api/contracts", payload),

  /** Phase 4/6.4 actions. Close books P&L once; a roll returns the new contract. */
  close: (id: number) => http.post<Contract>(`/api/contracts/${id}/close`, {}),
  recall: (id: number) => http.post<Contract>(`/api/contracts/${id}/recall`, {}),
  roll: (id: number, payload: ContractRollPayload) => http.post<Contract>(`/api/contracts/${id}/roll`, payload),
  rollOptions: (id: number, signal?: AbortSignal) =>
    http.get<RolloverRecommendation>(`/api/contracts/${id}/roll-options`, signal),
};
