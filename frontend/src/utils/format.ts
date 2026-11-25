/**
 * Financial formatting helpers. Presentation only — no business calculations.
 * INR uses the Indian numbering system (lakh/crore grouping).
 */

const inrCurrency = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 2,
});

const inrCurrency0 = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

const inrNumber = new Intl.NumberFormat("en-IN");

/** ₹1,23,456.78 — full precision INR. */
export function formatINR(value: number | null | undefined, decimals = true): string {
  if (value == null || Number.isNaN(value)) return "—";
  return decimals ? inrCurrency.format(value) : inrCurrency0.format(value);
}

/** Indian-grouped integer/quantity, e.g. 1,23,456. */
export function formatQty(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return inrNumber.format(value);
}

/** Price with fixed decimal precision (default 2). */
export function formatPrice(value: number | null | undefined, decimals = 2): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toLocaleString("en-IN", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

/** Percentage with sensible precision, e.g. 8.55%. */
export function formatPercent(value: number | null | undefined, decimals = 2): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value.toFixed(decimals)}%`;
}

/** Compact large numbers: 1.2Cr / 3.4L / 12.3K. Indian scale. */
export function formatCompactINR(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  const abs = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (abs >= 1e7) return `${sign}₹${(abs / 1e7).toFixed(2)}Cr`;
  if (abs >= 1e5) return `${sign}₹${(abs / 1e5).toFixed(2)}L`;
  if (abs >= 1e3) return `${sign}₹${(abs / 1e3).toFixed(2)}K`;
  return `${sign}₹${abs.toFixed(0)}`;
}

/** Sign metadata so callers can show +/- and ▲/▼ without relying on color alone. */
export function signOf(value: number | null | undefined): "pos" | "neg" | "flat" {
  if (value == null || Number.isNaN(value) || value === 0) return "flat";
  return value > 0 ? "pos" : "neg";
}

const dateFmt = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  day: "2-digit",
  month: "short",
  year: "numeric",
});

const dateTimeFmt = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

function toDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  // Backend emits naive UTC timestamps (no timezone). Treat bare datetimes as UTC.
  const hasTz = /[zZ]|[+-]\d{2}:?\d{2}$/.test(value);
  const iso = value.includes("T") && !hasTz ? `${value}Z` : value;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function formatDate(value: string | null | undefined): string {
  const d = toDate(value);
  return d ? dateFmt.format(d) : "—";
}

export function formatDateTime(value: string | null | undefined): string {
  const d = toDate(value);
  return d ? dateTimeFmt.format(d) : "—";
}

/** "just now" / "3m ago" / "2h ago" relative to now. */
export function formatRelative(value: string | null | undefined, now: number = Date.now()): string {
  const d = toDate(value);
  if (!d) return "—";
  const secs = Math.max(0, Math.round((now - d.getTime()) / 1000));
  if (secs < 10) return "just now";
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  return `${days}d ago`;
}

export { toDate };
