import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { RolloverPage } from "@/pages/RolloverPage";
import { renderWithRouter, makeRollover } from "./testUtils";

vi.mock("@/api/rollover", () => ({
  rolloverApi: { list: vi.fn() },
}));

import { rolloverApi } from "@/api/rollover";

beforeEach(() => {
  vi.mocked(rolloverApi.list).mockResolvedValue([makeRollover()]);
});

describe("RolloverPage", () => {
  it("renders a data-backed recommendation with its future series", async () => {
    renderWithRouter(<RolloverPage />);
    expect(await screen.findByText("RELIANCE")).toBeInTheDocument();
    // Future series from the (persisted) snapshot data.
    expect(screen.getByText("Dec-2026")).toBeInTheDocument();
    // Best option is tagged.
    expect(screen.getByText("Best")).toBeInTheDocument();
    // Favorable (fee >= current) tag is shown.
    expect(screen.getByText(/≥ current fee/)).toBeInTheDocument();
  });

  it("clearly marks insufficient-data cases instead of guessing", async () => {
    vi.mocked(rolloverApi.list).mockResolvedValue([
      makeRollover({
        status: "INSUFFICIENT_DATA",
        rationale: "No persisted future-series SLB data for RELIANCE.",
        best_option: null,
        fee_delta: null,
        favorable: null,
        options: [],
      }),
    ]);
    renderWithRouter(<RolloverPage />);
    expect(await screen.findByText(/No future-series market data/i)).toBeInTheDocument();
  });

  it("shows an empty state when no contract needs rollover", async () => {
    vi.mocked(rolloverApi.list).mockResolvedValue([]);
    renderWithRouter(<RolloverPage />);
    expect(
      await screen.findByText(/No contracts need rollover attention/i),
    ).toBeInTheDocument();
  });
});
