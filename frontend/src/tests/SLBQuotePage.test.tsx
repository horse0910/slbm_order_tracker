import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { SLBQuotePage } from "@/pages/SLBQuotePage";
import type { NSEHistory, QuoteSeries, SLBQuote } from "@/types";
import { renderWithRouter } from "./testUtils";

vi.mock("@/api/slb", () => ({ slbApi: { quote: vi.fn(), history: vi.fn(), liveBySymbol: vi.fn() } }));
vi.mock("@/utils/csv", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/utils/csv")>()),
  downloadCsv: vi.fn(),
}));
import { slbApi } from "@/api/slb";
import { downloadCsv } from "@/utils/csv";

function series(o: Partial<QuoteSeries>): QuoteSeries {
  return {
    series_code: "XN", series_family: "B", month: "Nov-2026", settlement_date: "2026-11-03",
    settlement_estimated: false,
    bids: [{ price: 0.35, quantity: 10850 }, { price: 0.3, quantity: 500 }],
    offers: [{ price: 0.7, quantity: 2000 }],
    best_bid: 0.35, best_bid_qty: 10850, best_ask: 0.7, best_ask_qty: 2000, ltp: 0.31,
    traded_quantity: 1436482, open_positions: 14820912, underlying_price: 77.06, annualized_yield: 4.89,
    corporate_action: null, is_active: true, ...o,
  };
}

const QUOTE: SLBQuote = {
  symbol: "IRFC", source: "quote", blocked: false, as_of: "01-Oct-2026 17:00:00",
  series: [series({}), series({ series_code: "11", series_family: "A", bids: [], offers: [] }),
    series({ series_code: "R3", series_family: "SPECIAL", month: "Oct-2026" })],
};
const HISTORY: NSEHistory = {
  symbol: "IRFC", from: "2026-09-05", to: "2026-10-05",
  rows: [{ trade_date: "2026-10-01", settlement_date: "2027-08-03", series_code: "X8", series_family: "B",
    open: 33.23, high: 33.23, low: 33.23, close: 33.23, previous_close: 1, quantity: 1, trades: 1, value: 33.23 }],
};

function renderAt(path = "/slb/IRFC") {
  return renderWithRouter(
    <Routes><Route path="/slb/:symbol" element={<SLBQuotePage />} /></Routes>, [path],
  );
}

beforeEach(() => {
  vi.mocked(slbApi.quote).mockResolvedValue(QUOTE);
  vi.mocked(slbApi.history).mockResolvedValue(HISTORY);
});
afterEach(() => vi.useRealTimers());

describe("SLBQuotePage", () => {
  it("lists Series B only with its NSE settlement date", async () => {
    renderAt();
    const xn = await screen.findByTestId("quote-row-XN");
    expect(within(xn).getByText("B")).toBeInTheDocument();
    expect(within(xn).getByText("03 Nov 2026")).toBeInTheDocument();
    expect(screen.queryByTestId("quote-row-11")).not.toBeInTheDocument();
    expect(screen.queryByTestId("quote-row-R3")).not.toBeInTheDocument();
    expect(slbApi.quote).toHaveBeenCalledWith("IRFC", expect.anything());
  });

  it("shows the 5-level depth of a series", async () => {
    const user = userEvent.setup();
    renderAt();
    await user.click(await screen.findByRole("button", { name: "Depth XN" }));
    const depth = screen.getByTestId("depth-XN");
    expect(within(depth).getByText("0.30")).toBeInTheDocument();
    expect(within(depth).getByText("10,850")).toBeInTheDocument();
  });

  it("loads NSE history for 1W / 1M / 2M and exports it as CSV", async () => {
    const user = userEvent.setup();
    renderAt();
    await screen.findByText("X8");
    const [, from1m, to] = vi.mocked(slbApi.history).mock.calls[0];
    expect(from1m).toBe(new Date(new Date(`${to}T00:00:00Z`).getTime() - 30 * 86400000).toISOString().slice(0, 10));
    await user.click(screen.getByRole("button", { name: "1W" }));
    await waitFor(() => expect(slbApi.history).toHaveBeenCalledTimes(2));
    const [, from1w] = vi.mocked(slbApi.history).mock.calls[1];
    expect(new Date(`${to}T00:00:00Z`).getTime() - new Date(`${from1w}T00:00:00Z`).getTime()).toBe(7 * 86400000);
    await user.click(screen.getByRole("button", { name: "2M" }));
    await waitFor(() => expect(slbApi.history).toHaveBeenCalledTimes(3));
    await user.click(screen.getByRole("button", { name: "Export CSV" }));
    const [filename, text] = vi.mocked(downloadCsv).mock.calls[0];
    expect(filename).toMatch(/^IRFC-slb-history-.*\.csv$/);
    expect(text).toContain("2026-10-01,X8,B,2027-08-03,33.23");
  });

  it("when NSE fails, shows the cached rows with their age", async () => {
    vi.mocked(slbApi.quote).mockResolvedValue({
      ...QUOTE, source: "cache", blocked: true, as_of: null,
      series: [series({ bids: undefined, offers: undefined, as_of: new Date(Date.now() - 3 * 3600_000).toISOString().slice(0, 19) })],
    });
    renderAt();
    expect(await screen.findByText(/NSE is unavailable/)).toBeInTheDocument();
    expect(screen.getByText(/cached data from 3h ago/)).toBeInTheDocument();
    expect(screen.getByTestId("quote-row-XN")).toBeInTheDocument();
  });
});
