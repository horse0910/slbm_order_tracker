import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiError } from "@/api/client";
import { PortfolioPage } from "@/pages/PortfolioPage";
import { renderWithRouter, makeHolding, makeStock } from "./testUtils";

vi.mock("@/api/portfolio", () => ({
  portfolioApi: {
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
    importCsv: vi.fn(),
  },
}));

import { portfolioApi } from "@/api/portfolio";

beforeEach(() => {
  vi.mocked(portfolioApi.list).mockResolvedValue([makeHolding()]);
});

describe("PortfolioPage", () => {
  it("loads and renders holdings", async () => {
    renderWithRouter(<PortfolioPage />);
    expect(await screen.findByText("RELIANCE")).toBeInTheDocument();
    expect(screen.getByText("Reliance Industries")).toBeInTheDocument();
    // EligibilityPill renders a status glyph + label ("● Eligible"); assert the
    // visible eligibility state without coupling to the exact glyph.
    expect(screen.getByText(/Eligible/)).toBeInTheDocument();
  });

  it("shows in demat, lent out and total owned with a Lent badge", async () => {
    vi.mocked(portfolioApi.list).mockResolvedValue([
      makeHolding({
        id: 7,
        symbol: "SBIN",
        name: "State Bank of India",
        quantity: 500,
        in_demat: 300,
        lent: 200,
        total_owned: 500,
        lendable: 300,
        lent_contracts: [
          {
            contract_id: 3,
            series: "XN",
            quantity_lent: 200,
            trade_date: "2026-10-01",
            settlement_date: "2026-11-03",
          },
        ],
      }),
      makeHolding({
        id: 8,
        symbol: "IRFC",
        name: "Indian Railway Finance",
        quantity: 0,
        in_demat: 0,
        lent: 1000,
        total_owned: 1000,
        lent_contracts: [
          {
            contract_id: 4,
            series: "XD",
            quantity_lent: 1000,
            trade_date: "2026-09-29",
            settlement_date: "2026-12-01",
          },
        ],
      }),
    ]);
    renderWithRouter(<PortfolioPage />);

    const sbinRow = (await screen.findByText("SBIN")).closest("tr") as HTMLElement;
    for (const header of ["In demat", "Lent out", "Total owned"]) {
      expect(screen.getByRole("columnheader", { name: header })).toBeInTheDocument();
    }
    expect(within(sbinRow).getAllByText("300")).toHaveLength(2); // in demat and lendable
    expect(within(sbinRow).getByText("200")).toBeInTheDocument();
    expect(within(sbinRow).getByText("500")).toBeInTheDocument();
    expect(within(sbinRow).getByText(/Lent 200 · XN · settles/)).toBeInTheDocument();

    // Fully lent: missing from Zerodha's export, still listed.
    const irfcRow = screen.getByText("IRFC").closest("tr") as HTMLElement;
    expect(within(irfcRow).getAllByText("1,000").length).toBe(2);
    expect(within(irfcRow).getByText(/Lent 1,000 · XD · settles/)).toBeInTheDocument();
  });

  it("adds a new stock", async () => {
    const user = userEvent.setup();
    vi.mocked(portfolioApi.create).mockResolvedValue(makeStock({ symbol: "INFY", id: 2 }));
    renderWithRouter(<PortfolioPage />);
    await screen.findByText("RELIANCE");

    await user.click(screen.getByRole("button", { name: /add stock/i }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/symbol/i), "INFY");
    await user.click(within(dialog).getByRole("button", { name: /add stock/i }));

    await waitFor(() =>
      expect(portfolioApi.create).toHaveBeenCalledWith(
        expect.objectContaining({ symbol: "INFY" }),
      ),
    );
  });

  it("shows backend validation errors in the add form", async () => {
    const user = userEvent.setup();
    vi.mocked(portfolioApi.create).mockRejectedValue(
      new ApiError("validation", "symbol: invalid", 422, [
        { field: "symbol", message: "Symbol already exists" },
      ]),
    );
    renderWithRouter(<PortfolioPage />);
    await screen.findByText("RELIANCE");

    await user.click(screen.getByRole("button", { name: /add stock/i }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/symbol/i), "DUP");
    await user.click(within(dialog).getByRole("button", { name: /add stock/i }));

    expect(await screen.findByText("Symbol already exists")).toBeInTheDocument();
  });

  it("edits an existing stock", async () => {
    const user = userEvent.setup();
    vi.mocked(portfolioApi.update).mockResolvedValue(makeStock({ quantity: 200 }));
    renderWithRouter(<PortfolioPage />);
    await screen.findByText("RELIANCE");

    await user.click(screen.getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog");
    const qty = within(dialog).getByLabelText(/quantity/i);
    await user.clear(qty);
    await user.type(qty, "200");
    await user.click(within(dialog).getByRole("button", { name: /save changes/i }));

    await waitFor(() =>
      expect(portfolioApi.update).toHaveBeenCalledWith(1, expect.objectContaining({ quantity: 200 })),
    );
  });

  it("deletes a stock after confirmation", async () => {
    const user = userEvent.setup();
    vi.mocked(portfolioApi.remove).mockResolvedValue(undefined);
    renderWithRouter(<PortfolioPage />);
    await screen.findByText("RELIANCE");

    await user.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(portfolioApi.remove).toHaveBeenCalledWith(1));
  });

  it("imports a CSV and reports added / rejected counts", async () => {
    const user = userEvent.setup();
    vi.mocked(portfolioApi.importCsv).mockResolvedValue({
      total_parsed: 3,
      added: 2,
      updated: 0,
      errors: ["Row 3: invalid quantity 'abc'"],
      stocks: [makeStock(), makeStock({ symbol: "TCS", id: 3 })],
      zeroed: [],
      zeroing_skipped: "1 row(s) could not be read, so stocks missing from the file were left unchanged.",
    });
    renderWithRouter(<PortfolioPage />);
    await screen.findByText("RELIANCE");

    await user.click(screen.getByRole("button", { name: /import csv/i }));
    const dialog = await screen.findByRole("dialog");
    const file = new File(["symbol,qty\nRELIANCE,10"], "holdings.csv", { type: "text/csv" });
    const input = dialog.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, file);
    await user.click(within(dialog).getByRole("button", { name: /import csv/i }));

    expect(await screen.findByText("Added 2")).toBeInTheDocument();
    expect(screen.getByText("Rejected 1")).toBeInTheDocument();
    expect(screen.getByText(/invalid quantity/i)).toBeInTheDocument();
    expect(portfolioApi.importCsv).toHaveBeenCalledWith(file);
    expect(screen.getByText(/stocks missing from the file were left unchanged/)).toBeInTheDocument();
  });

  it("lists the stocks an import set to 0", async () => {
    const user = userEvent.setup();
    vi.mocked(portfolioApi.importCsv).mockResolvedValue({
      total_parsed: 2,
      added: 0,
      updated: 2,
      errors: [],
      stocks: [makeStock()],
      zeroed: ["SBIN", "YESBANK"],
      zeroing_skipped: null,
    });
    renderWithRouter(<PortfolioPage />);
    await screen.findByText("RELIANCE");

    await user.click(screen.getByRole("button", { name: /import csv/i }));
    const dialog = await screen.findByRole("dialog");
    const file = new File(["symbol,qty\nRELIANCE,10"], "holdings.csv", { type: "text/csv" });
    await user.upload(dialog.querySelector('input[type="file"]') as HTMLInputElement, file);
    await user.click(within(dialog).getByRole("button", { name: /import csv/i }));

    expect(await within(dialog).findByText("Set to 0: 2")).toBeInTheDocument();
    expect(within(dialog).getByText("SBIN, YESBANK")).toBeInTheDocument();
  });
});

describe("PortfolioPage quote links and lendable (Phase 6.3 / 6.5)", () => {
  it("links the symbol to its quote page and shows lendable after pledges", async () => {
    vi.mocked(portfolioApi.list).mockResolvedValue([
      makeHolding({ symbol: "SBIN", quantity: 250, in_demat: 250, total_owned: 250, pledged_quantity: 100, lendable: 150 }),
    ]);
    renderWithRouter(<PortfolioPage />);
    const link = await screen.findByRole("link", { name: "SBIN" });
    expect(link).toHaveAttribute("href", "/slb/SBIN");
    expect(screen.getByRole("columnheader", { name: "Lendable" })).toBeInTheDocument();
    const row = link.closest("tr") as HTMLElement;
    expect(within(row).getByText("150")).toBeInTheDocument();
    expect(within(row).getByText("100 pledged")).toBeInTheDocument();
  });
});
