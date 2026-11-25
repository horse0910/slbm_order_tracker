import {
  Bar,
  BarChart,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { BestOpportunityItem } from "@/types";
import { formatPercent } from "@/utils/format";

/** Bars labelled "SYMBOL · SERIES" (two series of one stock stay apart), net yield to 2 dp. */
export function chartData(data: BestOpportunityItem[]) {
  return data.map((o) => ({
    name: o.series ? `${o.symbol} · ${o.series}` : o.symbol,
    yield: Number(o.annualized_yield.toFixed(2)),
  }));
}

/** Compact bar chart of the net annualised yields of the top opportunities. */
export function OpportunityYieldChart({ data }: { data: BestOpportunityItem[] }) {
  const rows = chartData(data);

  return (
    <div className="h-40 w-full" aria-hidden>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} margin={{ top: 4, right: 8, bottom: 0, left: -16 }}>
          <XAxis
            dataKey="name"
            tick={{ fill: "#94a3b8", fontSize: 11 }}
            axisLine={{ stroke: "#26304a" }}
            tickLine={false}
          />
          <YAxis
            tick={{ fill: "#94a3b8", fontSize: 11 }}
            axisLine={false}
            tickLine={false}
            width={44}
            tickFormatter={(v) => `${v}%`}
          />
          <Tooltip
            cursor={{ fill: "#1e273b55" }}
            contentStyle={{
              background: "#111725",
              border: "1px solid #26304a",
              borderRadius: 8,
              fontSize: 12,
            }}
            labelStyle={{ color: "#e2e8f0" }}
            formatter={(value) => [formatPercent(Number(value)), "Net yield p.a."]}
          />
          <Bar dataKey="yield" radius={[3, 3, 0, 0]} maxBarSize={48}>
            {rows.map((entry) => (
              <Cell key={entry.name} fill="#3fb98a" />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
