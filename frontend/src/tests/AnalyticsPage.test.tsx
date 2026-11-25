import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PortfolioAnalyticsPage as AnalyticsPage } from "@/pages/PortfolioAnalyticsPage";
import {
  renderWithRouter,
  makeAnalytics,
  makeHolding,
  makeSLBHistory,
  makeEquityHistory,
} from "./testUtils";

vi.mock("@/api/analytics", () => ({
  analyticsApi: {
    summary: vi.fn(),
    history: vi.fn(),
    equityHistory: vi.fn(),
    refreshEquityHistory: vi.fn(),
  },
}));

vi.mock("@/api/portfolio", () => ({
  portfolioApi: { list: vi.fn() },
}));

vi.mock("@/api/export", () => ({
  exportApi: {
    download: vi.fn(),
    pnlCsvUrl: () => "/api/export/pnl.csv",
    contractsCsvUrl: () => "/api/export/contracts.csv",
    analyticsCsvUrl: () => "/api/export/analytics.csv",
    historyCsvUrl: (s: string | null, d: number) =>
      `/api/export/history.csv?days=${d}${s ? `&symbol=${s}` : ""}`,
  },
}));

import { analyticsApi } from "@/api/analytics";
import { portfolioApi } from "@/api/portfolio";
import { exportApi } from "@/api/export";

beforeEach(() => {
  vi.mocked(analyticsApi.summary).mockResolvedValue(makeAnalytics());
  vi.mocked(analyticsApi.history).mockResolvedValue(makeSLBHistory());
  vi.mocked(analyticsApi.equityHistory).mockResolvedValue(makeEquityHistory());
  vi.mocked(analyticsApi.refreshEquityHistory).mockResolvedValue({
    refreshed: { RELIANCE: 3, INFY: 2 },
    symbols: 2,
    total_bars: 5,
  });
  vi.mocked(portfolioApi.list).mockResolvedValue([
    makeHolding({ id: 1, symbol: "RELIANCE", quantity: 100 }),
    makeHolding({ id: 2, symbol: "INFY", quantity: 50 }),
  ]);
});

describe("AnalyticsPage", () => {
  it("renders status breakdown, FY income and yield stats from the backend", async () => {
    renderWithRouter(<AnalyticsPage />);
    expect(await screen.findByText("2026-2027")).toBeInTheDocument();
    expect(screen.getByText("Expiring soon")).toBeInTheDocument();
    expect(screen.getAllByText("RELIANCE").length).toBeGreaterThan(0);
  });

  it("shows empty states when there is no recorded data", async () => {
    vi.mocked(analyticsApi.summary).mockResolvedValue(
      makeAnalytics({ status_breakdown: {}, income_by_financial_year: [], yield_by_symbol: [] }),
    );
    renderWithRouter(<AnalyticsPage />);
    expect(await screen.findByText(/No P&L recorded yet/i)).toBeInTheDocument();
    expect(screen.getByText(/No market data yet/i)).toBeInTheDocument();
  });

  it("shows the SLB yield trend for the owned portfolio by default and prompts for a symbol", async () => {
    renderWithRouter(<AnalyticsPage />);
    expect(await screen.findByText(/SLB yield trend — Owned portfolio/)).toBeInTheDocument();
    // Portfolio scope by default → no symbol query param on the history call.
    await waitFor(() => expect(analyticsApi.history).toHaveBeenCalledWith(null, 90, expect.anything()));
    // Equity history is gated until a symbol is chosen.
    expect(screen.getByText(/Pick a symbol/i)).toBeInTheDocument();
    expect(analyticsApi.equityHistory).not.toHaveBeenCalled();
  });

  it("fetches and renders equity price history when a symbol is selected", async () => {
    const user = userEvent.setup();
    renderWithRouter(<AnalyticsPage />);
    await screen.findByRole("heading", { name: /SLB yield trend/ });

    await user.selectOptions(screen.getByLabelText("Symbol"), "RELIANCE");
    await waitFor(() =>
      expect(analyticsApi.equityHistory).toHaveBeenCalledWith("RELIANCE", 90, expect.anything()),
    );
    // Descriptive stat grid renders the backend-computed period return (never client math).
    expect(await screen.findByText("Period return")).toBeInTheDocument();
    expect(screen.getByText("+1.59%")).toBeInTheDocument();
  });

  it("refreshes equity history from NSE and reports how many bars were fetched", async () => {
    const user = userEvent.setup();
    renderWithRouter(<AnalyticsPage />);
    await screen.findByRole("heading", { name: /SLB yield trend/ });

    await user.click(screen.getByRole("button", { name: /Refresh all from NSE/i }));
    await waitFor(() => expect(analyticsApi.refreshEquityHistory).toHaveBeenCalledWith(null, 90));
    expect(await screen.findByText(/Fetched 5 bars across 2 symbols\./)).toBeInTheDocument();
  });

  it("exports the analytics report and the SLB trend as CSV from the backend", async () => {
    const user = userEvent.setup();
    renderWithRouter(<AnalyticsPage />);
    await screen.findByRole("heading", { name: /SLB yield trend/ });

    // Two Export CSV buttons in source order: the page report, then the trend.
    const [reportBtn, trendBtn] = screen.getAllByRole("button", { name: /export csv/i });
    await user.click(reportBtn);
    expect(exportApi.download).toHaveBeenCalledWith("/api/export/analytics.csv");
    // Trend export defaults to the portfolio scope + 90-day window on screen.
    await user.click(trendBtn);
    expect(exportApi.download).toHaveBeenCalledWith("/api/export/history.csv?days=90");
  });

  it("opens the browser print dialog for a Save-as-PDF report", async () => {
    const user = userEvent.setup();
    const printSpy = vi.fn();
    window.print = printSpy;
    renderWithRouter(<AnalyticsPage />);
    await screen.findByRole("heading", { name: /SLB yield trend/ });

    await user.click(screen.getByRole("button", { name: /print \/ save as pdf/i }));
    expect(printSpy).toHaveBeenCalledTimes(1);
  });
});
