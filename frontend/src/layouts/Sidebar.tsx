import { NavLink } from "react-router-dom";
import { FUTURE_NAV, PRIMARY_NAV, type NavItem } from "@/utils/navigation";

function ActiveNavItem({ item }: { item: NavItem }) {
  return (
    <NavLink
      to={item.path}
      end={item.path === "/"}
      className={({ isActive }) =>
        `flex items-center justify-between rounded-md px-3 py-2 text-sm transition-colors ${
          isActive
            ? "bg-accent-soft text-accent"
            : "text-slate-300 hover:bg-surface-2 hover:text-accent"
        }`
      }
    >
      <span>{item.label}</span>
    </NavLink>
  );
}

function FutureNavItem({ item }: { item: NavItem }) {
  return (
    <div
      className="flex cursor-not-allowed items-center justify-between rounded-md px-3 py-2 text-sm text-slate-600"
      title={`Planned for Phase ${item.phase} — not implemented yet`}
      aria-disabled="true"
    >
      <span>{item.label}</span>
      <span className="pill bg-surface-2 text-slate-500">Soon</span>
    </div>
  );
}

export function Sidebar() {
  return (
    <aside className="flex h-full w-60 flex-col border-r border-line bg-surface-1">
      <div className="flex items-center gap-2 border-b border-line px-4 py-4">
        <div className="flex h-8 w-8 items-center justify-center rounded-md bg-accent text-sm font-bold text-white">
          ↗
        </div>
        <div className="leading-tight">
          <p className="text-sm font-semibold text-slate-100">SLBM Tracker</p>
          <p className="text-2xs text-slate-500">Your lending workspace</p>
        </div>
      </div>

      <nav className="flex-1 space-y-1 overflow-y-auto px-2 py-3">
        <p className="px-2 py-1 text-2xs font-semibold uppercase tracking-wide text-slate-600">
          Monitoring
        </p>
        {PRIMARY_NAV.map((item) => (
          <ActiveNavItem key={item.path} item={item} />
        ))}

        <p className="mt-4 px-2 py-1 text-2xs font-semibold uppercase tracking-wide text-slate-600">
          Management
        </p>
        {FUTURE_NAV.map((item) =>
          item.implemented ? (
            <ActiveNavItem key={item.path} item={item} />
          ) : (
            <FutureNavItem key={item.path} item={item} />
          ),
        )}
      </nav>

      <div className="border-t border-line px-4 py-3 text-2xs text-slate-600">
        Series B · Local workspace
      </div>
    </aside>
  );
}
