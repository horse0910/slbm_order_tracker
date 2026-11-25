import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import { LiveBoardPage } from "@/pages/LiveBoardPage";
import type { LiveBoard, LiveSeries, TradeFeed } from "@/types";
import { FakeWebSocket } from "./fakeWebSocket";
import { renderWithRouter } from "./testUtils";

vi.mock("@/api/live", () => ({
  liveApi: { board: vi.fn(), trades: vi.fn() },
}));

import { liveApi } from "@/api/live";

function series(overrides: Partial<LiveSeries>): LiveSeries {
  return {
    series_code: "XN",
    series_family: "B",
    series: "Nov-2026",
    settlement_date: "2026-11-03",
    settlement_estimated: false,
    ltp: 0.31,
    traded_quantity: 1000,
    open_positions: 14820912,
    best_bid: 0.35,
    best_bid_qty: 10850,
    best_ask: 0.7,
    best_ask_qty: 2000,
    annualized_yield: 4.89,
    updated_at: "2026-10-05T04:03:00",
    age_seconds: 45,
    ...overrides,
  };
}

function board(overrides: Partial<LiveBoard> = {}): LiveBoard {
  return {
    generated_at: "2026-10-05T04:03:45",
    market_open: true,
    refresh_seconds: 180,
    series_family: "B",
    last_sweep: null,
    sweep_warning: null,
    holdings: [
      {
        stock_id: 1,
        symbol: "IRFC",
        total_owned: 1000,
        in_demat: 1000,
        lent: 0,
        status: "live",
        slb_checked_at: null,
        series: [
          series({}),
          series({ series_code: "11", series_family: "A", ltp: 0, traded_quantity: 0, best_bid: 5, best_bid_qty: 6 }),
        ],
      },
      {
        stock_id: 2,
        symbol: "NOTSLB",
        total_owned: 5,
        in_demat: 5,
        lent: 0,
        status: "not_on_slb",
        slb_checked_at: "2026-10-05T04:00:00",
        series: [],
      },
    ],
    ...overrides,
  };
}

const FEED: TradeFeed = { since: "2026-10-04T18:30:00", inferred: true, events: [] };

let originalWS: unknown;
beforeEach(() => {
  originalWS = (globalThis as { WebSocket?: unknown }).WebSocket;
  (globalThis as { WebSocket?: unknown }).WebSocket = FakeWebSocket;
  FakeWebSocket.reset();
  vi.mocked(liveApi.board).mockResolvedValue(board());
  vi.mocked(liveApi.trades).mockResolvedValue(FEED);
});
afterEach(() => {
  (globalThis as { WebSocket?: unknown }).WebSocket = originalWS;
});

describe("LiveBoardPage", () => {
  it("shows Series B only across every holding and expiry", async () => {
    renderWithRouter(<LiveBoardPage />);
    const xn = await screen.findByTestId("row-IRFC-XN");
    expect(within(xn).getAllByText("1,000")).toHaveLength(1); // held (total owned) and traded today
    expect(within(xn).getByText("0.35 (10,850)")).toBeInTheDocument();
    expect(within(xn).getByText("0.70 (2,000)")).toBeInTheDocument();
    expect(within(xn).getByText("45s")).toBeInTheDocument();
    expect(screen.queryByTestId("row-IRFC-11")).not.toBeInTheDocument();

    expect(screen.queryByRole("button", { name: "All series" })).not.toBeInTheDocument();
  });

  it("lists holdings not on the SLB board at the bottom", async () => {
    renderWithRouter(<LiveBoardPage />);
    const section = await screen.findByTestId("not-on-slb");
    expect(within(section).getByText("NOTSLB")).toBeInTheDocument();
  });

  it("a market.trade message highlights the right row and joins the feed without a reload", async () => {
    renderWithRouter(<LiveBoardPage />);
    await screen.findByTestId("row-IRFC-XN");
    const ws = FakeWebSocket.instances.find((w) => w.url.endsWith("/api/ws"))!;
    act(() => ws._open());

    vi.mocked(liveApi.board).mockResolvedValue(
      board({ holdings: [{ ...board().holdings[0], series: [series({ ltp: 0.33, traded_quantity: 1500 })] }] }),
    );
    act(() =>
      ws._message({
        type: "market.trade",
        inferred: true,
        symbol: "IRFC",
        series_code: "XN",
        series_family: "B",
        series: "Nov-2026",
        settlement_date: "2026-11-03",
        quantity: 500,
        last_fee: 0.33,
        traded_quantity_total: 1500,
        occurred_at: "2026-10-05T04:06:00",
      }),
    );

    expect(screen.getByTestId("row-IRFC-XN")).toHaveAttribute("data-flash", "true");
    const feed = screen.getByTestId("trade-feed");
    expect(within(feed).getByText(/since last refresh/i)).toBeInTheDocument();
    expect(within(feed).getByText(/IRFC/)).toBeInTheDocument();
    expect(within(feed).getByText(/\+500 @ ₹0\.33/)).toBeInTheDocument();
    await waitFor(() => expect(within(screen.getByTestId("row-IRFC-XN")).getByText("1,500")).toBeInTheDocument());
    expect(liveApi.board).toHaveBeenCalledTimes(2);
  });

  it("shows the sweep warning and the market-closed note", async () => {
    vi.mocked(liveApi.board).mockResolvedValue(
      board({
        market_open: false,
        sweep_warning: "A full sweep of 100 symbols takes about 200 s at the 2 s gap, longer than live_refresh_seconds (180 s).",
      }),
    );
    renderWithRouter(<LiveBoardPage />);
    expect(await screen.findByText(/A full sweep of 100 symbols/)).toBeInTheDocument();
    expect(screen.getByText(/Market closed/)).toBeInTheDocument();
  });
});

describe("LiveBoardPage quote links (Phase 6.3)", () => {
  it("links each symbol to its quote page", async () => {
    renderWithRouter(<LiveBoardPage />);
    const row = await screen.findByTestId("row-IRFC-XN");
    expect(within(row).getByRole("link", { name: "IRFC" })).toHaveAttribute("href", "/slb/IRFC");
  });
});
