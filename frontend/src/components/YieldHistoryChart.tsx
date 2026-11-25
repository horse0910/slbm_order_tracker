import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { SLBHistoryPoint } from "@/types";
import { formatDate, formatPercent, formatPrice, formatQty } from "@/utils/format";

const TOOLTIP_STYLE = {
  background: "#111725",
  border: "1px solid #26304a",
  borderRadius: 8,
  fontSize: 12,
} as const;

/** MMM DD tick from an ISO date, without pulling in a date lib. */
function shortDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00Z`);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", timeZone: "UTC" });
}

/**
 * Daily SLB annualized-yield trend line with an accessible data table fallback.
 *
 * The chart is aria-hidden (decorative); screen-reader and keyboard users get
 * the same figures — plus spread and open-positions — from the table below.
 * Every value is a backend-aggregated daily mean; nothing is interpolated.
 */
export function YieldHistoryChart({ points }: { points: SLBHistoryPoint[] }) {
  const chartData = points
    .filter((p) => p.annualized_yield !== null)
    .map((p) => ({ date: shortDate(p.date), yield: Number((p.annualized_yield ?? 0).toFixed(2)) }));

  return (
    <div className="space-y-3">
      {chartData.length > 0 && (
        <div className="h-52 w-full" aria-hidden>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 6, right: 8, bottom: 0, left: -16 }}>
              <CartesianGrid stroke="#1e273b" strokeDasharray="3 3" vertical={false} />
              <XAxis
                dataKey="date"
                tick={{ fill: "#94a3b8", fontSize: 11 }}
                axisLine={{ stroke: "#26304a" }}
                tickLine={false}
                minTickGap={24}
              />
              <YAxis
                tick={{ fill: "#94a3b8", fontSize: 11 }}
                axisLine={false}
                tickLine={false}
                width={44}
                tickFormatter={(v) => `${v}%`}
                domain={["auto", "auto"]}
              />
              <Tooltip
                contentStyle={TOOLTIP_STYLE}
                labelStyle={{ color: "#e2e8f0" }}
                formatter={(value) => [formatPercent(Number(value)), "Yield p.a."]}
              />
              <Line
                type="monotone"
                dataKey="yield"
                stroke="#3fb98a"
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4 }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      <details className="rounded-md border border-line bg-surface-2/40">
        <summary className="cursor-pointer select-none px-3 py-2 text-xs text-slate-400">
          Show daily data ({formatQty(points.length)} day{points.length === 1 ? "" : "s"})
        </summary>
        <div className="overflow-x-auto border-t border-line">
          <table className="w-full border-collapse">
            <thead>
              <tr className="border-b border-line">
                <th className="th">Date</th>
                <th className="th text-right">Yield p.a.</th>
                <th className="th text-right">Spread</th>
                <th className="th text-right">Open positions</th>
                <th className="th text-right">Samples</th>
              </tr>
            </thead>
            <tbody>
              {points.map((p) => (
                <tr key={p.date} className="border-b border-line/50">
                  <td className="td font-medium">{formatDate(p.date)}</td>
                  <td className="td text-right tnum">
                    {p.annualized_yield !== null ? formatPercent(p.annualized_yield) : "—"}
                  </td>
                  <td className="td text-right tnum text-slate-400">
                    {p.spread !== null ? formatPrice(p.spread) : "—"}
                  </td>
                  <td className="td text-right tnum text-slate-400">
                    {p.open_positions !== null ? formatQty(p.open_positions) : "—"}
                  </td>
                  <td className="td text-right tnum text-slate-500">{formatQty(p.samples)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
