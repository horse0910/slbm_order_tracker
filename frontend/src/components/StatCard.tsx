import type { ReactNode } from "react";
import { Skeleton } from "./StateViews";

interface StatCardProps {
  label: string;
  value: ReactNode;
  hint?: string;
  /** Optional second line under the hint. */
  subhint?: string;
  loading?: boolean;
  accent?: "default" | "pos" | "warn";
}

export function StatCard({ label, value, hint, subhint, loading, accent = "default" }: StatCardProps) {
  const accentText =
    accent === "pos" ? "text-pos" : accent === "warn" ? "text-warn" : "text-slate-100";
  return (
    <div className="panel panel-pad flex flex-col justify-between">
      <p className="text-2xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
      {loading ? (
        <Skeleton className="mt-2 h-7 w-24" />
      ) : (
        <p className={`mt-1 text-2xl font-semibold tnum ${accentText}`}>{value}</p>
      )}
      {hint ? <p className="mt-1 text-2xs text-slate-500">{hint}</p> : null}
      {subhint ? <p className="text-2xs text-slate-500">{subhint}</p> : null}
    </div>
  );
}
