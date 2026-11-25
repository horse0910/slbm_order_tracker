import { describe, expect, it } from "vitest";
import {
  formatCompactINR,
  formatPercent,
  formatPrice,
  formatQty,
  signOf,
} from "@/utils/format";
import { assessFreshness, latestTimestamp } from "@/utils/freshness";

describe("format helpers", () => {
  it("formats percentages with fixed precision", () => {
    expect(formatPercent(8.5)).toBe("8.50%");
    expect(formatPercent(null)).toBe("—");
  });

  it("formats prices with two decimals by default", () => {
    expect(formatPrice(1.3)).toBe("1.30");
    expect(formatPrice(2600, 0)).toBe("2,600");
  });

  it("groups quantities in the Indian numbering system", () => {
    expect(formatQty(350000)).toBe("3,50,000");
    expect(formatQty(null)).toBe("—");
  });

  it("compacts large INR values with Indian scale", () => {
    expect(formatCompactINR(500000)).toBe("₹5.00L");
    expect(formatCompactINR(12500000)).toBe("₹1.25Cr");
    expect(formatCompactINR(999)).toBe("₹999");
    expect(formatCompactINR(-500000)).toBe("-₹5.00L");
  });

  it("classifies sign without relying on color", () => {
    expect(signOf(5)).toBe("pos");
    expect(signOf(-5)).toBe("neg");
    expect(signOf(0)).toBe("flat");
  });
});

describe("freshness", () => {
  it("flags old snapshots as stale", () => {
    const now = Date.now();
    const old = new Date(now - 5 * 60 * 1000).toISOString();
    const fresh = new Date(now - 5 * 1000).toISOString();
    expect(assessFreshness(old, 2 * 60 * 1000, now).isStale).toBe(true);
    expect(assessFreshness(fresh, 2 * 60 * 1000, now).isStale).toBe(false);
  });

  it("returns non-stale for missing timestamps", () => {
    expect(assessFreshness(null).isStale).toBe(false);
  });

  it("picks the latest timestamp across quotes", () => {
    const a = "2026-09-20T10:00:00";
    const b = "2026-09-20T10:05:00";
    expect(latestTimestamp([a, b, null])).toBe(b);
    expect(latestTimestamp([null, undefined])).toBeNull();
  });
});
