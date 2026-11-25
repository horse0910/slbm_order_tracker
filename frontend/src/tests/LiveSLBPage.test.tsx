import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiError } from "@/api/client";
import { LiveSLBPage } from "@/pages/LiveSLBPage";
import { renderWithRouter, makeHolding, makeLiveResponse, makeQuote, makeUniverse } from "./testUtils";

vi.mock("@/api/portfolio", () => ({
  portfolioApi: { list: vi.fn() },
}));
vi.mock("@/api/symbols", () => ({
  symbolsApi: { list: vi.fn() },
}));
vi.mock("@/api/slb", () => ({
  slbApi: { liveBySymbol: vi.fn() },
}));

import { portfolioApi } from "@/api/portfolio";
import { symbolsApi } from "@/api/symbols";
import { slbApi } from "@/api/slb";
import { opportunitiesApi } from "@/api/opportunities";
import { within } from "@testing-library/react";
import type { OpportunityBoard, OpportunityOption } from "@/types";

vi.mock("@/api/opportunities", () => ({ opportunitiesApi: { list: vi.fn() } }));

beforeEach(() => {
  // Selector is sourced from the tracked-symbol universe (union with holdings).
  vi.mocked(symbolsApi.list).mockResolvedValue(makeUniverse(["RELIANCE", "TCS"]));
  vi.mocked(portfolioApi.list).mockResolvedValue([
    makeHolding(),
    makeHolding({ symbol: "TCS", id: 2 }),
  ]);
});

describe("LiveSLBPage", () => {
  it("selects a symbol and loads SLB data with an order book", async () => {
    vi.mocked(slbApi.liveBySymbol).mockResolvedValue(makeLiveResponse());
    renderWithRouter(<LiveSLBPage />);

    await waitFor(() => expect(slbApi.liveBySymbol).toHaveBeenCalledWith("RELIANCE", expect.anything()));
    expect(await screen.findByText("Annualized yield (NSE)")).toBeInTheDocument();
    expect(screen.getByText("8.55%")).toBeInTheDocument();
    expect(screen.getByText(/Best bid · Borrower/i)).toBeInTheDocument();
    expect(screen.getByText(/Best ask · Lender/i)).toBeInTheDocument();
  });

  it("switches the selected series via the expiry tabs", async () => {
    vi.mocked(slbApi.liveBySymbol).mockResolvedValue(makeLiveResponse());
    const user = userEvent.setup();
    renderWithRouter(<LiveSLBPage />);
    await screen.findByText("8.55%");

    await user.click(screen.getByRole("button", { name: /Nov-2026/ }));
    expect(await screen.findByText("9.10%")).toBeInTheDocument();
  });

  it("shows an empty order book when there are no bids or asks", async () => {
    vi.mocked(slbApi.liveBySymbol).mockResolvedValue(
      makeLiveResponse({
        contracts: [
          makeQuote({ best_bid: 0, best_bid_qty: 0, best_ask: 0, best_ask_qty: 0 }),
        ],
      }),
    );
    renderWithRouter(<LiveSLBPage />);
    expect(await screen.findByText("No open orders")).toBeInTheDocument();
  });

  it("displays an error state when the SLB fetch fails", async () => {
    vi.mocked(slbApi.liveBySymbol).mockRejectedValue(new ApiError("network", "boom"));
    renderWithRouter(<LiveSLBPage />);
    expect(await screen.findByText(/Unable to connect to the SLBM backend/i)).toBeInTheDocument();
  });

  it("flags stale/cached data based on the snapshot timestamp", async () => {
    const oldTs = new Date(Date.now() - 6 * 60 * 1000).toISOString();
    vi.mocked(slbApi.liveBySymbol).mockResolvedValue(
      makeLiveResponse({ contracts: [makeQuote({ timestamp: oldTs })] }),
    );
    renderWithRouter(<LiveSLBPage />);
    expect(await screen.findByText(/Possibly stale/i)).toBeInTheDocument();
  });

  it("sources the selector from the tracked-symbol universe (not only holdings)", async () => {
    // A universe symbol the user does NOT own must still be selectable.
    vi.mocked(symbolsApi.list).mockResolvedValue(makeUniverse(["SBIN", "HDFCBANK"]));
    vi.mocked(portfolioApi.list).mockResolvedValue([]);
    vi.mocked(slbApi.liveBySymbol).mockResolvedValue(makeLiveResponse());
    renderWithRouter(<LiveSLBPage />);

    const select = await screen.findByLabelText("Symbol");
    const options = Array.from(select.querySelectorAll("option")).map((o) => o.textContent);
    expect(options).toEqual(["HDFCBANK", "SBIN"]); // alphabetical, universe-sourced
    // First option auto-selected drives the SLB fetch.
    await waitFor(() =>
      expect(slbApi.liveBySymbol).toHaveBeenCalledWith("HDFCBANK", expect.anything()),
    );
  });

  it("unions owned holdings with the universe so an owned-only symbol stays monitorable", async () => {
    vi.mocked(symbolsApi.list).mockResolvedValue(makeUniverse(["SBIN"]));
    // WIPRO is owned but not in the universe — it must still appear.
    vi.mocked(portfolioApi.list).mockResolvedValue([makeHolding({ symbol: "WIPRO", id: 9 })]);
    vi.mocked(slbApi.liveBySymbol).mockResolvedValue(makeLiveResponse());
    renderWithRouter(<LiveSLBPage />);

    const select = await screen.findByLabelText("Symbol");
    const options = Array.from(select.querySelectorAll("option")).map((o) => o.textContent);
    expect(options).toEqual(["SBIN", "WIPRO"]);
  });
});

describe("LiveSLBPage Expiries tab (Phase 6.2)", () => {
  const opt = (o: Partial<OpportunityOption>): OpportunityOption => ({
    series_code: "XN", series: "Nov-2026", settlement_date: "2026-11-03", settlement_estimated: false,
    days: 33, days_to_settlement: 33, best_bid: 0.35, best_bid_qty: 10850, best_ask: 0.7, spot: 77.06,
    lendable: 1000, fillable: 1000, gross_fee: 350, net_fee: 252.06, gross_yield_pa: 5.02, net_yield_pa: 3.62,
    breakeven_price: 0.02, price_for_floor: 0.38, corporate_action: null, quote_at: "2026-10-05T05:00:00",
    quote_age_seconds: 60, actionable: false, reasons: ["net yield 3.62% is below the 4.00% floor"], ticket: null,
    ...o,
  });
  const board: OpportunityBoard = {
    generated_at: "2026-10-05T05:01:00", expiries: 3, min_net_yield_pct: 4, min_fill_qty: 1, min_net_fee_inr: 0,
    series_family: "B",
    stocks: [{
      symbol: "IRFC", stock_id: 1, in_demat: 1000, pledged: 0, lendable: 1000,
      options: [
        opt({ series_code: "XO", settlement_date: "2026-10-06", net_yield_pa: 4.51, actionable: true, reasons: [], ticket: "IRFC | lend 1000 | XO | ₹0.05/share" }),
        opt({}),
        opt({ series_code: "XD", settlement_date: "2026-12-01", net_yield_pa: 6.82, net_fee: 878.54, best_bid: 1.17, actionable: true, reasons: [], ticket: "IRFC | lend 1000 | XD | ₹1.17/share" }),
      ],
    }],
  };

  it("shows one card per stock with a chip per expiry and the best actionable one outlined", async () => {
    const user = userEvent.setup();
    vi.mocked(opportunitiesApi.list).mockResolvedValue(board);
    renderWithRouter(<LiveSLBPage />);
    await user.click(await screen.findByRole("tab", { name: "Expiries" }));
    const card = await screen.findByTestId("expiry-card-IRFC");
    const chips = within(card).getAllByRole("button", { name: /XO|XN|XD/ });
    expect(chips.map((c) => c.getAttribute("data-best"))).toEqual(["false", "false", "true"]);
    expect(within(card).getByRole("button", { name: /XD/ })).toHaveTextContent("6.82%");
  });

  it("tapping a chip opens its full details: reasons, or the ticket line", async () => {
    const user = userEvent.setup();
    vi.mocked(opportunitiesApi.list).mockResolvedValue(board);
    renderWithRouter(<LiveSLBPage />);
    await user.click(await screen.findByRole("tab", { name: "Expiries" }));
    const card = await screen.findByTestId("expiry-card-IRFC");
    await user.click(within(card).getByRole("button", { name: /XN/ }));
    expect(within(card).getByText("net yield 3.62% is below the 4.00% floor")).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: /XD/ }));
    expect(within(card).getByText("IRFC | lend 1000 | XD | ₹1.17/share")).toBeInTheDocument();
    expect(within(card).getByText(/₹878.54/)).toBeInTheDocument();
  });
});

describe("LiveSLBPage default symbol (Phase 6.2)", () => {
  it("opens on a holding that is on the SLB board, not the first name alphabetically", async () => {
    vi.mocked(symbolsApi.list).mockResolvedValue(makeUniverse(["ARCIL", "IRFC"]));
    vi.mocked(portfolioApi.list).mockResolvedValue([
      makeHolding({ id: 1, symbol: "ARCIL", is_slb_eligible: false }),
      makeHolding({ id: 2, symbol: "IRFC", is_slb_eligible: true }),
    ]);
    renderWithRouter(<LiveSLBPage />);
    await waitFor(() => expect(slbApi.liveBySymbol).toHaveBeenCalledWith("IRFC", expect.anything()));
    expect(slbApi.liveBySymbol).not.toHaveBeenCalledWith("ARCIL", expect.anything());
  });
});
