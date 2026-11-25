import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { EquityBar } from "@/types";
import { formatDate, formatPrice, formatQty } from "@/utils/format";

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
 * Daily closing-price trend line with an accessible OHLC/volume table fallback.
 *
 * The chart is aria-hidden (decorative); the table below carries the same bars
 * for screen-reader/keyboard users. Bars are persisted exactly as fetched from
 * NSE — missing fields render as "—", never fabricated.
 */
export function PriceHistoryChart({ bars }: { bars: EquityBar[] }) {
  const chartData = bars
    .filter((b) => b.close !== null)
    .map((b) => ({ date: shortDate(b.date), close: Number((b.close ?? 0).toFixed(2)) }));

  return (
    <div className="space-y-3">
      {chartData.length > 0 && (
        <div className="h-52 w-full" aria-hidden>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 6, right: 8, bottom: 0, left: -8 }}>
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
                width={60}
                domain={["auto", "auto"]}
                tickFormatter={(v) => formatPrice(Number(v), 0)}
              />
              <Tooltip
                contentStyle={TOOLTIP_STYLE}
                labelStyle={{ color: "#e2e8f0" }}
                formatter={(value) => [`₹${formatPrice(Number(value))}`, "Close"]}
              />
              <Line
                type="monotone"
                dataKey="close"
                stroke="#5b8def"
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
          Show daily bars ({formatQty(bars.length)} day{bars.length === 1 ? "" : "s"})
        </summary>
        <div className="overflow-x-auto border-t border-line">
          <table className="w-full border-collapse">
            <thead>
              <tr className="border-b border-line">
                <th className="th">Date</th>
                <th className="th text-right">Open</th>
                <th className="th text-right">High</th>
                <th className="th text-right">Low</th>
                <th className="th text-right">Close</th>
                <th className="th text-right">Volume</th>
              </tr>
            </thead>
            <tbody>
              {bars.map((b) => (
                <tr key={b.date} className="border-b border-line/50">
                  <td className="td font-medium">{formatDate(b.date)}</td>
                  <td className="td text-right tnum text-slate-400">
                    {b.open !== null ? formatPrice(b.open) : "—"}
                  </td>
                  <td className="td text-right tnum text-slate-400">
                    {b.high !== null ? formatPrice(b.high) : "—"}
                  </td>
                  <td className="td text-right tnum text-slate-400">
                    {b.low !== null ? formatPrice(b.low) : "—"}
                  </td>
                  <td className="td text-right tnum font-semibold">
                    {b.close !== null ? formatPrice(b.close) : "—"}
                  </td>
                  <td className="td text-right tnum text-slate-500">
                    {b.volume !== null ? formatQty(b.volume) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
