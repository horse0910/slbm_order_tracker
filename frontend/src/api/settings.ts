import { http } from "./client";
import type { BusinessSettings, BusinessSettingsUpdate } from "@/types";

/**
 * Settings API — read + edit the non-secret business preferences (Phase 5D).
 *
 * Exposes ONLY the whitelisted business preferences that drive the contract,
 * alert and P&L engines. Credentials/secrets are never sent to or returned from
 * here: the backend excludes them from the response schema and forbids them in
 * the update schema (a request that tries to set one is rejected).
 */
export const settingsApi = {
  /** Effective (env + persisted overrides) business configuration. */
  get: (signal?: AbortSignal) => http.get<BusinessSettings>("/api/settings", signal),
  /** Apply a validated partial update; returns the new effective config. */
  update: (payload: BusinessSettingsUpdate, signal?: AbortSignal) =>
    http.put<BusinessSettings>("/api/settings", payload, signal),
  /** Reset every override back to the environment defaults; returns them. */
  reset: (signal?: AbortSignal) => http.del<BusinessSettings>("/api/settings", signal),
};
