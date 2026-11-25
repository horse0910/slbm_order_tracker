import { useState } from "react";

/** Copy a paste-ready Zerodha ticket line, e.g. "IRFC | lend 1000 | XD | ₹1.17/share" (Phase 6.1). */
export function CopyTicketButton({ ticket }: { ticket: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");

  async function copy() {
    try {
      await navigator.clipboard.writeText(ticket);
      setState("copied");
    } catch {
      setState("failed");
    }
    setTimeout(() => setState("idle"), 2000);
  }

  return (
    <button
      type="button"
      className="rounded px-2 py-1 text-2xs text-slate-300 hover:bg-surface-3"
      onClick={copy}
      title={ticket}
      aria-label={`Copy ticket: ${ticket}`}
    >
      {state === "copied" ? "Copied" : state === "failed" ? "Copy failed" : "Copy ticket"}
    </button>
  );
}
