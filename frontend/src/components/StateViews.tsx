import type { ReactNode } from "react";
import type { ApiError } from "@/api/client";

/** Animated skeleton bar. */
export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-surface-3 ${className}`} aria-hidden />;
}

/** Skeleton rows for a table body while the first load is in flight. */
export function TableSkeleton({ rows = 5, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <div className="p-4" role="status" aria-label="Loading data">
      <div className="space-y-2">
        {Array.from({ length: rows }).map((_, r) => (
          <div key={r} className="flex gap-3">
            {Array.from({ length: cols }).map((__, c) => (
              <Skeleton key={c} className="h-6 flex-1" />
            ))}
          </div>
        ))}
      </div>
      <span className="sr-only">Loading…</span>
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-2xs text-slate-400" role="status">
      <span className="h-3 w-3 animate-spin rounded-full border-2 border-slate-600 border-t-accent" />
      {label ? <span>{label}</span> : null}
    </span>
  );
}

interface EmptyStateProps {
  title: string;
  message: string;
  action?: ReactNode;
  icon?: ReactNode;
}

export function EmptyState({ title, message, action, icon }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      <div className="text-2xl text-slate-600" aria-hidden>
        {icon ?? "◍"}
      </div>
      <h3 className="text-sm font-semibold text-slate-200">{title}</h3>
      <p className="max-w-md text-xs text-slate-500">{message}</p>
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

interface ErrorStateProps {
  error: ApiError;
  onRetry?: () => void;
  compact?: boolean;
}

export function ErrorState({ error, onRetry, compact = false }: ErrorStateProps) {
  return (
    <div
      role="alert"
      className={`flex flex-col items-start gap-2 rounded-md border border-neg/40 bg-neg/10 ${
        compact ? "px-3 py-2" : "px-4 py-6"
      }`}
    >
      <div className="flex items-center gap-2">
        <span className="pill bg-neg/20 text-neg">Error</span>
        <p className="text-sm text-slate-200">{error.userMessage}</p>
      </div>
      {error.fieldErrors.length > 0 ? (
        <ul className="ml-1 list-disc pl-4 text-2xs text-slate-400">
          {error.fieldErrors.map((f, i) => (
            <li key={i}>
              <span className="font-medium text-slate-300">{f.field}</span>: {f.message}
            </li>
          ))}
        </ul>
      ) : null}
      {onRetry ? (
        <button type="button" className="btn-ghost mt-1" onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </div>
  );
}
