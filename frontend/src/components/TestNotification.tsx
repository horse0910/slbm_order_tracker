import { useState } from "react";
import { notificationsApi, type ChannelResult } from "@/api/notifications";
import { toApiError } from "@/api/client";
import { useApiResource } from "@/hooks/useApiResource";

const LABEL: Record<string, string> = { macos: "macOS", whatsapp: "WhatsApp" };

/** Channel status and a 'Send a test notification' button (Phase 5.5). */
export function TestNotification() {
  const status = useApiResource((signal) => notificationsApi.status(signal), []);
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<ChannelResult[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function send() {
    setBusy(true);
    setError(null);
    try {
      setResults((await notificationsApi.test()).results);
    } catch (err) {
      setError(toApiError(err).userMessage);
    } finally {
      setBusy(false);
    }
  }

  const s = status.data;
  return (
    <div className="space-y-2 px-4 py-3">
      {s ? (
        <p className="text-2xs text-slate-400">
          macOS: {s.macos_available ? (s.macos_enabled ? "on" : "turned off") : "not on a Mac"} · WhatsApp:{" "}
          {s.whatsapp_configured ? "configured" : "not configured"} · Email:{" "}
          {s.email_configured ? "configured" : "not configured"}
        </p>
      ) : null}
      <button type="button" className="btn-ghost" onClick={send} disabled={busy}>
        Send a test notification
      </button>
      {results ? (
        <ul className="text-2xs text-slate-300">
          {results.map((r) => (
            <li key={r.channel}>
              {LABEL[r.channel] ?? r.channel}: {r.outcome}
              {r.outcome !== "sent" ? <span className="text-slate-500"> ({r.detail})</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
      {error ? <p className="text-2xs text-neg">{error}</p> : null}
    </div>
  );
}
