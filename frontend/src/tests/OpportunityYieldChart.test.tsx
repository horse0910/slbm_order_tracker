import { describe, expect, it } from "vitest";
import { chartData } from "@/components/OpportunityYieldChart";
import { makeDashboard } from "./testUtils";

describe("OpportunityYieldChart", () => {
  it("labels each bar SYMBOL · SERIES so two series of one stock are told apart", () => {
    const base = makeDashboard().best_opportunities[0];
    const rows = chartData([
      { ...base, symbol: "IRFC", series: "XD", annualized_yield: 7.137 },
      { ...base, symbol: "IRFC", series: "XO", annualized_yield: 4.51 },
      { ...base, symbol: "SBIN", series: null, annualized_yield: 5 },
    ]);
    expect(rows.map((r) => r.name)).toEqual(["IRFC · XD", "IRFC · XO", "SBIN"]);
    expect(rows[0].yield).toBe(7.14);
  });
});
