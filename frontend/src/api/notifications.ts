import { http } from "./client";

/** Which channels are on (booleans only; never credentials). */
export interface NotificationStatus {
  macos_available: boolean;
  macos_enabled: boolean;
  whatsapp_configured: boolean;
  email_configured: boolean;
}

export interface ChannelResult {
  channel: string;
  delivered: boolean;
  outcome: "sent" | "skipped" | "failed";
  detail: string;
}

export const notificationsApi = {
  status: (signal?: AbortSignal) => http.get<NotificationStatus>("/api/notifications/status", signal),
  /** Fire the macOS channel, plus WhatsApp when configured; every attempt is logged. */
  test: () => http.post<{ results: ChannelResult[] }>("/api/notifications/test", {}),
};
