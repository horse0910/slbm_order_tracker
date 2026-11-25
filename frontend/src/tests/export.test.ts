import { afterEach, describe, expect, it, vi } from "vitest";
import { exportApi } from "@/api/export";

/**
 * CSV export helper tests. The URL builders honor the (empty by default) API
 * base and land under `/api/export`; `download` opens a transient anchor and
 * cleans it up (the backend's Content-Disposition owns the filename, so nothing
 * here needs to name the file).
 */
describe("exportApi URLs", () => {
  it("builds the fixed CSV endpoint URLs under /api/export", () => {
    expect(exportApi.pnlCsvUrl()).toBe("/api/export/pnl.csv");
    expect(exportApi.contractsCsvUrl()).toBe("/api/export/contracts.csv");
    expect(exportApi.analyticsCsvUrl()).toBe("/api/export/analytics.csv");
  });

  it("adds days + symbol params to the history URL", () => {
    expect(exportApi.historyCsvUrl("RELIANCE", 30)).toBe(
      "/api/export/history.csv?days=30&symbol=RELIANCE",
    );
  });

  it("omits the symbol param for the portfolio aggregate", () => {
    expect(exportApi.historyCsvUrl(null, 90)).toBe("/api/export/history.csv?days=90");
  });
});

describe("exportApi.download", () => {
  afterEach(() => vi.restoreAllMocks());

  it("clicks a transient anchor carrying the target URL, then removes it", () => {
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    const append = vi.spyOn(document.body, "appendChild");

    exportApi.download("/api/export/pnl.csv");

    expect(click).toHaveBeenCalledTimes(1);
    const anchor = append.mock.calls[0][0] as HTMLAnchorElement;
    expect(anchor.tagName).toBe("A");
    expect(anchor.getAttribute("href")).toBe("/api/export/pnl.csv");
    // No orphaned <a> left in the document after the click.
    expect(document.body.contains(anchor)).toBe(false);
  });
});
