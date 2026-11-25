/** Sidebar navigation model. All monitoring and management pages (Dashboard,
 * Portfolio, Live SLB, Contracts, Rollover, Alerts, Analytics, P&L, Settings)
 * are implemented; FUTURE_NAV items flagged `implemented: false` still render as
 * disabled placeholders. */

export interface NavItem {
  label: string;
  path: string;
  /** Implemented in this phase. Placeholders are visible but not navigable. */
  implemented: boolean;
  phase: number;
}

export const PRIMARY_NAV: NavItem[] = [
  { label: "Market", path: "/live", implemented: true, phase: 2 },
  { label: "Dashboard", path: "/", implemented: true, phase: 2 },
  { label: "Portfolio", path: "/portfolio", implemented: true, phase: 2 },
  { label: "Stock explorer", path: "/slb", implemented: true, phase: 2 },
];

export const FUTURE_NAV: NavItem[] = [
  { label: "My contracts", path: "/contracts", implemented: true, phase: 3 },
  { label: "Rollover", path: "/rollover", implemented: true, phase: 3 },
  { label: "Alerts", path: "/alerts", implemented: true, phase: 3 },
  { label: "Analytics", path: "/analytics", implemented: true, phase: 3 },
  { label: "P&L", path: "/pnl", implemented: true, phase: 3 },
  { label: "Settings", path: "/settings", implemented: true, phase: 3 },
];

export function pageTitle(pathname: string): string {
  const all = [...PRIMARY_NAV, ...FUTURE_NAV];
  const match = all.find((n) => n.path === pathname);
  return match?.label ?? "SLBM Order Tracker";
}
