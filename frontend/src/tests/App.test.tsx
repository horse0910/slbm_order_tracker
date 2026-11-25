import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "@/App";
import { renderWithRouter, makeDashboard, makeHolding, makeUniverse } from "./testUtils";

vi.mock("@/api/dashboard", () => ({
  dashboardApi: { summary: vi.fn() },
  healthApi: { check: vi.fn() },
}));
vi.mock("@/api/portfolio", () => ({
  portfolioApi: { list: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn(), importCsv: vi.fn() },
}));
vi.mock("@/api/slb", () => ({
  slbApi: { liveBySymbol: vi.fn() },
}));
vi.mock("@/api/symbols", () => ({
  symbolsApi: { list: vi.fn() },
}));

import { dashboardApi, healthApi } from "@/api/dashboard";
import { portfolioApi } from "@/api/portfolio";
import { symbolsApi } from "@/api/symbols";

beforeEach(() => {
  vi.mocked(healthApi.check).mockResolvedValue({
    status: "healthy",
    version: "0.1.0",
    database: "ok",
    timestamp: new Date().toISOString(),
  });
  vi.mocked(dashboardApi.summary).mockResolvedValue(makeDashboard());
  vi.mocked(portfolioApi.list).mockResolvedValue([makeHolding()]);
  vi.mocked(symbolsApi.list).mockResolvedValue(makeUniverse(["RELIANCE", "TCS"]));
});

describe("App shell", () => {
  it("renders the sidebar navigation and backend status", async () => {
    renderWithRouter(<App />);
    expect(screen.getByText("SLBM Tracker")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Portfolio" })).toBeInTheDocument();
    // Phase 3 management pages are now implemented and navigable (not placeholders).
    expect(screen.getByRole("link", { name: "My contracts" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Alerts" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/API v0\.1\.0/)).toBeInTheDocument());
  });

  it("loads the dashboard with backend data", async () => {
    renderWithRouter(<App />);
    await waitFor(() => expect(dashboardApi.summary).toHaveBeenCalled());
    expect(await screen.findByText("Best lending opportunities")).toBeInTheDocument();
    // best opportunity symbol from mock
    expect(await screen.findByText("TCS")).toBeInTheDocument();
  });

  it("navigates to the Portfolio page", async () => {
    const user = userEvent.setup();
    renderWithRouter(<App />);
    await user.click(screen.getByRole("link", { name: "Portfolio" }));
    expect(await screen.findByText("Portfolio holdings")).toBeInTheDocument();
    await waitFor(() => expect(portfolioApi.list).toHaveBeenCalled());
  });

  it("redirects an unknown route to the dashboard", async () => {
    // All Phase 3 nav pages are now implemented, so there are no placeholders.
    // An unknown path falls through to the catch-all redirect to the dashboard.
    renderWithRouter(<App />, ["/nonexistent-route"]);
    expect(await screen.findByText("Best lending opportunities")).toBeInTheDocument();
  });
});
