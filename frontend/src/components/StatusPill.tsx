interface EligibilityPillProps {
  eligible: boolean;
}

/** SLB eligibility indicator — text + shape, not color-only. */
export function EligibilityPill({ eligible }: EligibilityPillProps) {
  return eligible ? (
    <span className="pill bg-pos/15 text-pos" title="Eligible for Securities Lending & Borrowing">
      ● Eligible
    </span>
  ) : (
    <span className="pill bg-surface-3 text-slate-400" title="Not SLB eligible">
      ○ Not eligible
    </span>
  );
}

import type { ContractStatus } from "@/types";

/** Human labels + glyphs for each derived contract status (not color-only). */
const CONTRACT_STATUS_META: Record<
  ContractStatus,
  { label: string; className: string; glyph: string }
> = {
  ACTIVE: { label: "Active", className: "bg-pos/15 text-pos", glyph: "●" },
  EXPIRING_SOON: { label: "Expiring soon", className: "bg-warn/20 text-warn", glyph: "◷" },
  EXPIRED: { label: "Expired", className: "bg-neg/20 text-neg", glyph: "✕" },
  RECALLED: { label: "Recalled", className: "bg-surface-3 text-slate-300", glyph: "↩" },
  CLOSED: { label: "Closed", className: "bg-surface-3 text-slate-400", glyph: "○" },
  ROLLED_OVER: { label: "Rolled over", className: "bg-accent-soft text-white", glyph: "⟳" },
};

/** Effective lifecycle status of a lending contract (backend-derived). */
export function ContractStatusPill({ status }: { status: ContractStatus }) {
  const meta = CONTRACT_STATUS_META[status] ?? {
    label: status,
    className: "bg-surface-3 text-slate-300",
    glyph: "•",
  };
  return (
    <span className={`pill ${meta.className}`} title={meta.label}>
      {meta.glyph} {meta.label}
    </span>
  );
}

interface DaysBadgeProps {
  days: number;
}

// Display-only urgency tiers for the days-remaining badge. EXPIRING_SOON_DAYS
// mirrors the backend `expiring_soon_days` config default (7); the backend
// remains the sole source of truth for actual contract status — these thresholds
// only drive badge styling, never a status decision, so they are kept as local
// constants rather than threading live config through every page that renders one.
const CRITICAL_DAYS = 1;
const EXPIRING_SOON_DAYS = 7;

/** Days-remaining badge with urgency tiers (icon + text, not color-only). */
export function DaysBadge({ days }: DaysBadgeProps) {
  if (days <= CRITICAL_DAYS) {
    return <span className="pill bg-neg/20 text-neg">⚠ {days}d</span>;
  }
  if (days <= EXPIRING_SOON_DAYS) {
    return <span className="pill bg-warn/20 text-warn">◷ {days}d</span>;
  }
  return <span className="pill bg-surface-3 text-slate-300">{days}d</span>;
}
