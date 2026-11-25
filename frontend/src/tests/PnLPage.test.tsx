import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PnLPage } from "@/pages/PnLPage";
import { renderWithRouter } from "./testUtils";

vi.mock("@/api/pnl", () => ({
  pnlApi: { list: vi.fn(), preview: vi.fn(), recordForContract: vi.fn() },
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

import { pnlApi } from "@/api/pnl";
import { exportApi } from "@/api/export";

const RECORD = {
  id: 1,
  contract_id: 1,
  symbol: "RELIANCE",
  series: "Nov-2026",
  gross_fee: 500,
  processing_charge: 100,
  gst: 18,
  dp_charge: 0.1,
  net_income: 381.9,
  tax_slab_pct: 30,
  estimated_tax: 114.57,
  post_tax_income: 267.33,
  created_at: "2026-11-01T10:00:00",
};

beforeEach(() => {
  vi.mocked(pnlApi.list).mockResolvedValue({
    totals: {
      records: 1,
      gross_fee: 500,
      processing_charge: 100,
      gst: 18,
      dp_charge: 0.1,
      net_income: 381.9,
      estimated_tax: 114.57,
      post_tax_income: 267.33,
    },
    records: [RECORD],
  });
});

describe("PnLPage", () => {
  it("shows totals and per-contract records from the backend", async () => {
    renderWithRouter(<PnLPage />);
    expect(await screen.findByText("RELIANCE")).toBeInTheDocument();
    // Net income total surfaced in a stat card (compact) and the row (full).
    expect(screen.getAllByText(/381/).length).toBeGreaterThan(0);
  });

  it("previews a breakdown via the backend engine (no client math)", async () => {
    const user = userEvent.setup();
    vi.mocked(pnlApi.preview).mockResolvedValue({
      quantity_lent: 100,
      lending_fee: 5,
      processing_fee_pct: 20,
      gst_pct: 18,
      dp_charge_flat_inr: 13,
      tax_slab_pct: 30,
      gross_fee: 500,
      processing_charge: 100,
      gst: 18,
      dp_charge: 15.34,
      net_income: 366.66,
      estimated_tax: 110,
      post_tax_income: 256.66,
    });
    renderWithRouter(<PnLPage />);
    await screen.findByText("RELIANCE");

    await user.click(screen.getByRole("button", { name: /calculate/i }));
    await waitFor(() => expect(pnlApi.preview).toHaveBeenCalledWith(100, 5, undefined));
    // The itemized processing line is unique to the preview breakdown.
    expect(await screen.findByText(/Processing \(20%\)/)).toBeInTheDocument();
    expect(screen.getByText("DP charge (₹13 + GST per scrip)")).toBeInTheDocument();
  });

  it("downloads P&L as CSV from the backend export endpoint", async () => {
    const user = userEvent.setup();
    renderWithRouter(<PnLPage />);
    await screen.findByText("RELIANCE");

    await user.click(screen.getByRole("button", { name: /export csv/i }));
    expect(exportApi.download).toHaveBeenCalledWith("/api/export/pnl.csv");
  });
});
