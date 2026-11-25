import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ContractsPage } from "@/pages/ContractsPage";
import { renderWithRouter, makeContract, makeHolding, makeRollover } from "./testUtils";

vi.mock("@/api/contracts", () => ({
  contractsApi: {
    list: vi.fn(),
    get: vi.fn(),
    create: vi.fn(),
    close: vi.fn(),
    recall: vi.fn(),
    roll: vi.fn(),
    rollOptions: vi.fn(),
  },
}));
vi.mock("@/api/portfolio", () => ({
  portfolioApi: { list: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn(), importCsv: vi.fn() },
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

import { contractsApi } from "@/api/contracts";
import { portfolioApi } from "@/api/portfolio";
import { exportApi } from "@/api/export";

beforeEach(() => {
  vi.mocked(contractsApi.list).mockResolvedValue([makeContract()]);
  vi.mocked(portfolioApi.list).mockResolvedValue([makeHolding()]);
});

describe("ContractsPage", () => {
  it("renders contracts with backend-derived status and income", async () => {
    renderWithRouter(<ContractsPage />);
    expect(await screen.findByText("RELIANCE")).toBeInTheDocument();
    // Backend-derived status pill (title disambiguates it from the filter tab).
    expect(screen.getByTitle("Active")).toBeInTheDocument();
    // Fee income comes straight from the payload (₹525 -> compact ₹525).
    expect(screen.getByText("₹525")).toBeInTheDocument();
  });

  it("filters by effective status via the backend query param", async () => {
    const user = userEvent.setup();
    renderWithRouter(<ContractsPage />);
    await screen.findByText("RELIANCE");

    await user.click(screen.getByRole("tab", { name: "Expiring soon" }));
    await waitFor(() =>
      expect(contractsApi.list).toHaveBeenCalledWith("EXPIRING_SOON", expect.anything()),
    );
  });

  it("flags rollover candidates", async () => {
    vi.mocked(contractsApi.list).mockResolvedValue([
      makeContract({ effective_status: "EXPIRING_SOON", is_rollover_candidate: true, days_remaining: 3 }),
    ]);
    renderWithRouter(<ContractsPage />);
    expect(await screen.findByText("⟳ Rollover")).toBeInTheDocument();
  });

  it("downloads contracts as CSV from the backend export endpoint", async () => {
    const user = userEvent.setup();
    renderWithRouter(<ContractsPage />);
    await screen.findByText("RELIANCE");

    await user.click(screen.getByRole("button", { name: /export csv/i }));
    expect(exportApi.download).toHaveBeenCalledWith("/api/export/contracts.csv");
  });

  it("opens a contract through the form", async () => {
    const user = userEvent.setup();
    vi.mocked(contractsApi.create).mockResolvedValue(makeContract({ id: 2 }));
    renderWithRouter(<ContractsPage />);
    await screen.findByText("RELIANCE");

    await user.click(screen.getByRole("button", { name: /open contract/i }));
    const dialog = await screen.findByRole("dialog");
    // Stock auto-selected (single holding); fill required numeric + expiry.
    await user.type(within(dialog).getByLabelText(/quantity lent/i), "50");
    await user.type(within(dialog).getByLabelText(/lending fee/i), "4.5");
    const expiry = within(dialog).getByLabelText(/expiry date/i);
    await user.type(expiry, "2026-12-01");
    await user.click(within(dialog).getByRole("button", { name: /open contract/i }));

    await waitFor(() =>
      expect(contractsApi.create).toHaveBeenCalledWith(
        expect.objectContaining({ stock_id: 1, quantity_lent: 50, lending_fee: 4.5, expiry_date: "2026-12-01" }),
      ),
    );
  });

  it("shows the backend warning after saving a contract traded before the last import", async () => {
    const user = userEvent.setup();
    const warning =
      "Traded before your last holdings import (06 Oct). Check that Total owned matches Zerodha.";
    vi.mocked(contractsApi.create).mockResolvedValue(makeContract({ id: 2, warning }));
    renderWithRouter(<ContractsPage />);
    await screen.findByText("RELIANCE");

    await user.click(screen.getByRole("button", { name: /open contract/i }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/quantity lent/i), "50");
    await user.type(within(dialog).getByLabelText(/lending fee/i), "4.5");
    await user.type(within(dialog).getByLabelText(/expiry date/i), "2026-12-01");
    await user.click(within(dialog).getByRole("button", { name: /open contract/i }));

    const alert = await screen.findByRole("status");
    expect(alert).toHaveTextContent(warning);
    await user.click(within(alert).getByRole("button", { name: /dismiss/i }));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("shows no warning when the backend returns none", async () => {
    const user = userEvent.setup();
    vi.mocked(contractsApi.create).mockResolvedValue(makeContract({ id: 2, warning: null }));
    renderWithRouter(<ContractsPage />);
    await screen.findByText("RELIANCE");

    await user.click(screen.getByRole("button", { name: /open contract/i }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/quantity lent/i), "50");
    await user.type(within(dialog).getByLabelText(/lending fee/i), "4.5");
    await user.type(within(dialog).getByLabelText(/expiry date/i), "2026-12-01");
    await user.click(within(dialog).getByRole("button", { name: /open contract/i }));

    await waitFor(() => expect(contractsApi.create).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.queryByText(/Traded before your last holdings import/)).not.toBeInTheDocument();
  });

  it("shows an empty state and disables opening when there are no holdings", async () => {
    vi.mocked(contractsApi.list).mockResolvedValue([]);
    vi.mocked(portfolioApi.list).mockResolvedValue([]);
    renderWithRouter(<ContractsPage />);
    expect(await screen.findByText(/No lending contracts yet/i)).toBeInTheDocument();
    // With no holdings the header action is disabled.
    const openButtons = screen.getAllByRole("button", { name: /open contract/i });
    expect(openButtons.some((b) => (b as HTMLButtonElement).disabled)).toBe(true);
  });
});

describe("ContractsPage actions (Phase 6.4)", () => {
  const option = (overrides = {}) => ({
    series: "Dec-2026",
    series_code: "XD",
    expiry_date: "2026-12-01",
    market_fee: 33,
    annualized_yield: null,
    open_positions: 0,
    data_as_of: "2026-10-30T06:00:00",
    best_bid: 1.17,
    best_bid_qty: 1376,
    fillable: 100,
    net_fee: 72.0,
    net_yield_pa: 14.86,
    chain_days: 61,
    within_cap: true,
    ...overrides,
  });

  function rec(options = [option()], status: "ROLL_AVAILABLE" | "BLOCKED_BY_CAP" = "ROLL_AVAILABLE") {
    return makeRollover({
      status,
      options,
      best_option: status === "ROLL_AVAILABLE" ? options[0] : null,
      origin_date: "2026-10-01",
      days_used: 28,
      days_used_label: "28 of 365 days used",
    });
  }

  it("closes a contract after confirming", async () => {
    const user = userEvent.setup();
    vi.mocked(contractsApi.close).mockResolvedValue(makeContract({ stored_status: "CLOSED" }));
    renderWithRouter(<ContractsPage />);
    await user.click(await screen.findByRole("button", { name: "Close RELIANCE" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/books its P&L once/i)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Close contract" }));
    await waitFor(() => expect(contractsApi.close).toHaveBeenCalledWith(1));
  });

  it("recalls a contract after confirming", async () => {
    const user = userEvent.setup();
    vi.mocked(contractsApi.recall).mockResolvedValue(makeContract({ stored_status: "RECALLED" }));
    renderWithRouter(<ContractsPage />);
    await user.click(await screen.findByRole("button", { name: "Recall RELIANCE" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Recall contract" }));
    await waitFor(() => expect(contractsApi.recall).toHaveBeenCalledWith(1));
  });

  it("rolls into the pre-filled next Series B contract at its bid", async () => {
    const user = userEvent.setup();
    vi.mocked(contractsApi.rollOptions).mockResolvedValue(rec());
    vi.mocked(contractsApi.roll).mockResolvedValue(makeContract({ id: 2 }));
    renderWithRouter(<ContractsPage />);
    await user.click(await screen.findByRole("button", { name: "Roll RELIANCE" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("28 of 365 days used")).toBeInTheDocument();
    expect(within(dialog).getByLabelText(/new fee per share/i)).toHaveValue(1.17);
    expect(within(dialog).getByText(/XD · settles 01 Dec 2026/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Roll contract" }));
    await waitFor(() =>
      expect(contractsApi.roll).toHaveBeenCalledWith(1, {
        expiry_date: "2026-12-01",
        reverse_leg_date: "2026-12-01",
        lending_fee: 1.17,
        series: "Dec-2026",
        series_code: "XD",
      }),
    );
  });

  it("blocks a roll past the 365-day cap", async () => {
    const user = userEvent.setup();
    vi.mocked(contractsApi.rollOptions).mockResolvedValue(
      rec([option({ series_code: "X2", series: "Feb-2027", expiry_date: "2027-10-05", chain_days: 369, within_cap: false })], "BLOCKED_BY_CAP"),
    );
    renderWithRouter(<ContractsPage />);
    await user.click(await screen.findByRole("button", { name: "Roll RELIANCE" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText(/past the 365-day cap/i)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Roll contract" })).toBeDisabled();
  });

  it("offers no actions on a closed contract", async () => {
    vi.mocked(contractsApi.list).mockResolvedValue([makeContract({ stored_status: "CLOSED", effective_status: "CLOSED" })]);
    renderWithRouter(<ContractsPage />);
    await screen.findByText("RELIANCE");
    expect(screen.queryByRole("button", { name: "Close RELIANCE" })).not.toBeInTheDocument();
  });
});
