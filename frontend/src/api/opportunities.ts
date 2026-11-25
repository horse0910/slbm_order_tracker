import { http } from "./client";
import type { OpportunityBoard } from "@/types";

/** Phase 3.5: each held stock across its next Series B expiries (backend-priced). */
export const opportunitiesApi = {
  list: (expiries?: number, signal?: AbortSignal) =>
    http.get<OpportunityBoard>(`/api/opportunities${expiries ? `?expiries=${expiries}` : ""}`, signal),
};
