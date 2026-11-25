import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DashboardPage } from "@/pages/DashboardPage";
import { renderWithRouter, makeDashboard } from "./testUtils";

vi.mock("@/api/dashboard", () => ({
  dashboardApi: { summary: vi.fn() },
}));

import { dashboardApi } from "@/api/dashboard";

beforeEach(() => {
  vi.mocked(dashboardApi.summary).mockResolvedValue(
    makeDashboard({
      lent_out: {
        stocks: 3,
        shares: 1210,
        value_at_last_price: 282750,
        unpriced_symbols: ["NOPRICE"],
        fee_income_due_gross: 630,
        fee_income_due_net: 435.3,
      },
    }),
  );
});

describe("DashboardPage", () => {
  it("shows shares held as total owned, split into in demat and lent", async () => {
    vi.mocked(dashboardApi.summary).mockResolvedValue(
      makeDashboard({
        portfolio: {
          total_stocks: 58,
          slb_eligible_stocks: 58,
          total_quantity: 500,
          in_demat: 300,
          lent: 1200,
          total_owned: 1500,
          portfolio_cost_value: 500000,
        },
      }),
    );
    renderWithRouter(<DashboardPage />);
    expect(await screen.findByText("1,500 shares held")).toBeInTheDocument();
    expect(screen.getByText("in demat 300 · lent 1,200")).toBeInTheDocument();
  });

  it("shows a Lent out card from the backend summary", async () => {
    renderWithRouter(<DashboardPage />);
    expect(await screen.findByRole("heading", { name: "Lent out" })).toBeInTheDocument();
    const region = within(screen.getByTestId("lent-out-card"));
    expect(region.getByText("3")).toBeInTheDocument();
    expect(region.getByText("1,210")).toBeInTheDocument();
    expect(region.getByText("₹2.83L")).toBeInTheDocument();
    expect(region.getByText(/435\.30/)).toBeInTheDocument();
    expect(region.getByText(/No last price for NOPRICE/)).toBeInTheDocument();
  });

  it("shows an empty Lent out card when nothing is lent", async () => {
    vi.mocked(dashboardApi.summary).mockResolvedValue(makeDashboard());
    renderWithRouter(<DashboardPage />);
    const region = within(await screen.findByTestId("lent-out-card"));
    expect(region.getByText(/No shares are out on loan/)).toBeInTheDocument();
  });
});

describe("DashboardPage best opportunities (Phase 3.5)", () => {
  it("shows net yield at the bid, the net fee and the fill", async () => {
    vi.mocked(dashboardApi.summary).mockResolvedValue(
      makeDashboard({
        best_opportunities: [
          {
            symbol: "IRFC",
            series: "XD",
            expiry_date: "2026-12-01",
            ltp: 1.17,
            best_bid: 1.17,
            best_ask: 1.3,
            annualized_yield: 6.82,
            open_positions: 0,
            timestamp: new Date().toISOString(),
            fillable: 1000,
            net_fee: 878.54,
            days_to_settlement: 61,
            ticket: "IRFC | lend 1000 | XD | ₹1.17/share",
          },
        ],
      }),
    );
    renderWithRouter(<DashboardPage />);
    expect(await screen.findByRole("columnheader", { name: "Net yield p.a." })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Bid" })).toBeInTheDocument();
    const yieldCell = screen.getByTestId("net-yield-IRFC-XD");
    expect(within(yieldCell).getByText("6.82%")).toBeInTheDocument();
    expect(within(yieldCell).getByText("61 d")).toBeInTheDocument();
    expect(within(yieldCell).getByText("₹878.54")).toBeInTheDocument();
    expect(screen.getByText("1,000")).toBeInTheDocument();
  });
});

describe("DashboardPage copy ticket (Phase 6.1)", () => {
  it("copies the Zerodha ticket line of an opportunity", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    vi.mocked(dashboardApi.summary).mockResolvedValue(
      makeDashboard({
        best_opportunities: [{
          symbol: "IRFC", series: "XD", expiry_date: "2026-12-01", ltp: 1.17, best_bid: 1.17, best_ask: 1.3,
          annualized_yield: 6.82, open_positions: 0, timestamp: new Date().toISOString(), fillable: 1000,
          net_fee: 878.54, days_to_settlement: 61, ticket: "IRFC | lend 1000 | XD | ₹1.17/share",
        }],
      }),
    );
    renderWithRouter(<DashboardPage />);
    await user.click(await screen.findByRole("button", { name: "Copy ticket: IRFC | lend 1000 | XD | ₹1.17/share" }));
    expect(writeText).toHaveBeenCalledWith("IRFC | lend 1000 | XD | ₹1.17/share");
    expect(await screen.findByText("Copied")).toBeInTheDocument();
  });
});
