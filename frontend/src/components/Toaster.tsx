import { useEffect } from "react";

/**
 * Lightweight in-app toast stack (Phase 4). No external dependency — a fixed
 * overlay rendered by LiveAlertsProvider. Presentational only: the provider
 * owns the toast list; each card self-dismisses after a TTL and can be closed
 * manually.
 */

export interface ToastItem {
  id: number;
  title: string;
  body: string;
}

const TOAST_TTL_MS = 8000;

function ToastCard({ toast, onDismiss }: { toast: ToastItem; onDismiss: (id: number) => void }) {
  useEffect(() => {
    const timer = setTimeout(() => onDismiss(toast.id), TOAST_TTL_MS);
    return () => clearTimeout(timer);
  }, [toast.id, onDismiss]);

  return (
    <div
      role="status"
      className="pointer-events-auto w-80 rounded-md border border-line bg-surface-2 px-3 py-2.5 shadow-lg shadow-black/30"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 text-xs font-semibold text-warn">
            <span aria-hidden="true">🔔</span>
            <span className="truncate">{toast.title}</span>
          </p>
          <p className="mt-0.5 break-words text-2xs text-slate-300">{toast.body}</p>
        </div>
        <button
          type="button"
          aria-label="Dismiss notification"
          className="rounded px-1 text-slate-500 hover:text-slate-200"
          onClick={() => onDismiss(toast.id)}
        >
          ✕
        </button>
      </div>
    </div>
  );
}

export function Toaster({
  toasts,
  onDismiss,
}: {
  toasts: ToastItem[];
  onDismiss: (id: number) => void;
}) {
  if (toasts.length === 0) return null;
  return (
    <div
      aria-live="polite"
      className="pointer-events-none fixed bottom-4 right-4 z-50 flex flex-col gap-2"
    >
      {toasts.map((t) => (
        <ToastCard key={t.id} toast={t} onDismiss={onDismiss} />
      ))}
    </div>
  );
}
