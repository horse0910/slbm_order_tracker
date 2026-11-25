import type { ReactElement, ReactNode } from "react";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type {
  Alert,
  AnalyticsSummary,
  Contract,
  DashboardSummary,
  EquityHistory,
  Holding,
  RolloverRecommendation,
  SLBHistory,
  SLBLiveResponse,
  SLBQuoteItem,
  Stock,
  SymbolUniverse,
} from "@/types";

export function renderWithRouter(ui: ReactElement, initialEntries: string[] = ["/"]) {
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <MemoryRouter initialEntries={initialEntries}>{children}</MemoryRouter>
  );
  return render(ui, { wrapper: Wrapper });
}

export function makeStock(overrides: Partial<Stock> = {}): Stock {
  return {
    id: 1,
    symbol: "RELIANCE",
    name: "Reliance Industries",
    isin: "INE002A01018",
    quantity: 100,
    avg_buy_price: 2500,
    is_slb_eligible: true,
    notes: null,
    created_at: "2026-09-01T10:00:00",
    updated_at: "2026-09-01T10:00:00",
    ...overrides,
  };
}

export function makeHolding(overrides: Partial<Holding> = {}): Holding {
  const stock = makeStock(overrides);
  return {
    ...stock,
    holdings_imported_at: null,
    in_demat: stock.quantity,
    lent: 0,
    total_owned: stock.quantity,
    lendable: stock.quantity,
    pledged_quantity: 0,
    lent_contracts: [],
    ...overrides,
  };
}

export function makeUniverse(symbols: string[] = ["RELIANCE", "TCS"]): SymbolUniverse {
  return {
    count: symbols.length,
    symbols: symbols.map((symbol, i) => ({
      id: i + 1,
      symbol,
      created_at: "2026-09-01T10:00:00",
    })),
  };
}

export function makeContract(overrides: Partial<Contract> = {}): Contract {
  return {
    id: 1,
    stock_id: 1,
    symbol: "RELIANCE",
    series: "Nov-2026",
    trade_date: "2026-10-01",
    expiry_date: "2026-11-03",
    reverse_leg_date: null,
    quantity_lent: 100,
    lending_fee: 5.25,
    stored_status: "ACTIVE",
    effective_status: "ACTIVE",
    days_remaining: 30,
    tenure_days: 33,
    lending_fee_income: 525,
    is_rollover_candidate: false,
    notes: null,
    ...overrides,
  };
}

export function makeRollover(
  overrides: Partial<RolloverRecommendation> = {},
): RolloverRecommendation {
  const options = overrides.options ?? [
    {
      series: "Dec-2026",
      expiry_date: "2026-12-01",
      market_fee: 6.0,
      annualized_yield: 9.5,
      open_positions: 8000,
      data_as_of: "2026-11-01T09:30:00",
    },
  ];
  return {
    contract_id: 1,
    stock_id: 1,
    symbol: "RELIANCE",
    current_series: "Nov-2026",
    current_expiry: "2026-11-03",
    current_fee: 5.25,
    days_remaining: 3,
    status: "ROLL_AVAILABLE",
    rationale: "Best available future series pays ₹6/share vs ₹5.25/share now (≥ current).",
    best_option: options[0] ?? null,
    fee_delta: 0.75,
    favorable: true,
    options,
    ...overrides,
  };
}

export function makeAlert(overrides: Partial<Alert> = {}): Alert {
  return {
    id: 1,
    stock_id: null,
    alert_type: "YIELD_THRESHOLD",
    threshold: 8.0,
    is_active: true,
    is_triggered: false,
    last_triggered_at: null,
    trigger_message: null,
    created_at: "2026-09-01T10:00:00",
    ...overrides,
  };
}

export function makeAnalytics(overrides: Partial<AnalyticsSummary> = {}): AnalyticsSummary {
  return {
    generated_at: "2026-11-01T10:00:00",
    status_breakdown: { ACTIVE: 2, EXPIRING_SOON: 1 },
    income_by_financial_year: [
      {
        financial_year: "2026-2027",
        contracts: 1,
        gross_fee: 500,
        net_income: 381.9,
        estimated_tax: 114.57,
        post_tax_income: 267.33,
      },
    ],
    yield_by_symbol: [
      { symbol: "RELIANCE", latest_yield: 8.55, average_yield: 8.2, samples: 3 },
    ],
    ...overrides,
  };
}

export function makeSLBHistory(overrides: Partial<SLBHistory> = {}): SLBHistory {
  return {
    scope: "portfolio",
    symbol: null,
    days: 90,
    points: [
      { date: "2026-08-01", annualized_yield: 8.1, spread: 0.15, open_positions: 11000, samples: 4 },
      { date: "2026-08-02", annualized_yield: 8.4, spread: 0.12, open_positions: 11800, samples: 5 },
      { date: "2026-08-03", annualized_yield: 8.9, spread: 0.1, open_positions: 12200, samples: 3 },
    ],
    ...overrides,
  };
}

export function makeEquityHistory(overrides: Partial<EquityHistory> = {}): EquityHistory {
  return {
    symbol: "RELIANCE",
    days: 90,
    bars: [
      {
        symbol: "RELIANCE",
        date: "2026-08-01",
        open: 2500,
        high: 2540,
        low: 2490,
        close: 2520,
        vwap: 2515,
        volume: 1_200_000,
        delivery_qty: 600_000,
        delivery_pct: 50,
      },
      {
        symbol: "RELIANCE",
        date: "2026-08-02",
        open: 2520,
        high: 2580,
        low: 2515,
        close: 2560,
        vwap: 2550,
        volume: 1_400_000,
        delivery_qty: 700_000,
        delivery_pct: 50,
      },
    ],
    stats: {
      symbol: "RELIANCE",
      samples: 2,
      period_start: "2026-08-01",
      period_end: "2026-08-02",
      first_close: 2520,
      last_close: 2560,
      period_high: 2580,
      period_low: 2490,
      period_return_pct: 1.59,
      avg_volume: 1_300_000,
      daily_return_volatility_pct: 0.5,
    },
    ...overrides,
  };
}

export function makeQuote(overrides: Partial<SLBQuoteItem> = {}): SLBQuoteItem {
  return {
    symbol: "RELIANCE",
    series: "Oct-2026",
    expiry_date: "2026-10-06",
    best_bid: 1.25,
    best_bid_qty: 500,
    best_ask: 1.4,
    best_ask_qty: 300,
    ltp: 1.3,
    underlying_price: 2600,
    spread: 0.15,
    spread_pct: 0.12,
    open_positions: 12000,
    volume: 4500,
    turnover: 585000,
    annualized_yield: 8.55,
    corporate_action: null,
    is_slb_eligible: true,
    timestamp: new Date().toISOString(),
    ...overrides,
  };
}

export function makeLiveResponse(overrides: Partial<SLBLiveResponse> = {}): SLBLiveResponse {
  const contracts = overrides.contracts ?? [
    makeQuote(),
    makeQuote({ series: "Nov-2026", expiry_date: "2026-11-03", annualized_yield: 9.1 }),
  ];
  return {
    symbol: overrides.symbol ?? "RELIANCE",
    contracts_count: contracts.length,
    contracts,
  };
}

export function makeDashboard(overrides: Partial<DashboardSummary> = {}): DashboardSummary {
  return {
    generated_at: new Date().toISOString(),
    portfolio: {
      total_stocks: 3,
      slb_eligible_stocks: 2,
      total_quantity: 350,
      in_demat: 350,
      lent: 0,
      total_owned: 350,
      portfolio_cost_value: 500000,
    },
    contracts: { active: 1, expiring_soon: 1, expired: 0 },
    income: {
      total_gross_fee: 1200,
      total_net_income: 900,
      current_fy_net_income: 600,
      financial_year: "2026-2027",
    },
    lent_out: {
      stocks: 0,
      shares: 0,
      value_at_last_price: 0,
      unpriced_symbols: [],
      fee_income_due_gross: 0,
      fee_income_due_net: 0,
    },
    upcoming_expiries: [
      {
        contract_id: 1,
        symbol: "RELIANCE",
        series: "Oct-2026",
        expiry_date: "2026-10-06",
        quantity_lent: 50,
        lending_fee: 1.3,
        days_remaining: 4,
      },
    ],
    best_opportunities: [
      {
        symbol: "TCS",
        series: "Oct-2026",
        expiry_date: "2026-10-06",
        ltp: 2.1,
        best_bid: 2.0,
        best_ask: 2.2,
        annualized_yield: 9.8,
        open_positions: 5000,
        timestamp: new Date().toISOString(),
      },
    ],
    recent_alerts: [],
    ...overrides,
  };
}
