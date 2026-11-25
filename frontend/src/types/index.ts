/**
 * Centralized TypeScript types mirroring the FastAPI backend contract.
 *
 * These are the single source of truth for API shapes on the frontend and are
 * derived directly from `slbm_tracker/api/schemas.py`. Do not add derived /
 * business-logic fields here — the backend owns those calculations.
 */

// ---------------------------------------------------------------------------
// Portfolio (schemas.py: StockResponse / StockCreate / StockUpdate)
// ---------------------------------------------------------------------------

export interface Stock {
  id: number;
  symbol: string;
  name: string | null;
  isin: string | null;
  quantity: number;
  avg_buy_price: number;
  is_slb_eligible: boolean;
  notes: string | null;
  created_at: string; // ISO datetime
  updated_at: string; // ISO datetime
  /** When the last Zerodha import set `quantity` (naive-UTC ISO), if ever. */
  holdings_imported_at?: string | null;
  /** Pledged (collateral) shares from the last import (Phase 3.2). */
  pledged_quantity?: number;
}

/** An ACTIVE contract shown as a Lent badge (schemas.py: LentContractItem). */
export interface LentContract {
  contract_id: number;
  series: string | null;
  quantity_lent: number;
  trade_date: string; // ISO date
  /** reverse_leg_date when recorded, else expiry_date. */
  settlement_date: string; // ISO date
}

/**
 * A stock with where its shares are (schemas.py: HoldingResponse). Zerodha's
 * export leaves lent shares out, so the backend adds them from contracts:
 * in_demat = imported quantity − ACTIVE contracts traded on/after the import
 * date (IST); total_owned = in_demat + lent.
 */
export interface Holding extends Stock {
  in_demat: number;
  lent: number;
  total_owned: number;
  /** in_demat − pledged: what can be lent now (Phase 3.2). */
  lendable?: number;
  lent_contracts: LentContract[];
}

export interface StockCreate {
  symbol: string;
  name?: string | null;
  isin?: string | null;
  quantity: number;
  avg_buy_price: number;
  is_slb_eligible: boolean;
  notes?: string | null;
}

export interface StockUpdate {
  name?: string | null;
  isin?: string | null;
  quantity?: number;
  avg_buy_price?: number;
  is_slb_eligible?: boolean;
  notes?: string | null;
}

export interface PortfolioImportResult {
  total_parsed: number;
  added: number;
  updated: number;
  errors: string[];
  stocks: Stock[];
  /** Stocks missing from the file, set to quantity 0 (never deleted). */
  zeroed: string[];
  /** Why zeroing was skipped (no holdings, or unreadable rows); null if it ran. */
  zeroing_skipped: string | null;
}

// ---------------------------------------------------------------------------
// SLB market data (schemas.py: SLBQuoteItem / SLBLiveResponse)
// ---------------------------------------------------------------------------

export interface SLBQuoteItem {
  symbol: string;
  series: string;
  expiry_date: string | null; // ISO date
  best_bid: number;
  best_bid_qty: number;
  best_ask: number;
  best_ask_qty: number;
  ltp: number;
  underlying_price: number;
  spread: number;
  spread_pct: number;
  open_positions: number;
  volume: number;
  turnover: number;
  annualized_yield: number;
  corporate_action: string | null;
  is_slb_eligible: boolean;
  timestamp: string; // ISO datetime
}

export interface SLBLiveResponse {
  symbol: string;
  contracts_count: number;
  contracts: SLBQuoteItem[];
}

// ---------------------------------------------------------------------------
// Tracked symbol universe (schemas.py: TrackedSymbolResponse / SymbolUniverseResponse)
//
// The configured/seeded list of symbols the app monitors for SLB. This is NOT
// portfolio ownership (see Stock) and carries no market data — only tickers.
// ---------------------------------------------------------------------------

export interface TrackedSymbol {
  id: number;
  symbol: string;
  created_at: string; // ISO datetime
}

export interface SymbolUniverse {
  count: number;
  symbols: TrackedSymbol[];
}

// ---------------------------------------------------------------------------
// Lending contracts (schemas.py: ContractCreate / ContractResponse)
//
// The backend owns ALL lifecycle logic. `effective_status`, `days_remaining`,
// `tenure_days`, `lending_fee_income` and `is_rollover_candidate` are derived
// server-side (IST, date-consistent) — the frontend only displays them and
// must never recompute status from dates.
// ---------------------------------------------------------------------------

/** Derived lifecycle states (time-based) plus terminal business states. */
export type ContractStatus =
  | "ACTIVE"
  | "EXPIRING_SOON"
  | "EXPIRED"
  | "RECALLED"
  | "CLOSED"
  | "ROLLED_OVER";

export interface Contract {
  id: number;
  stock_id: number;
  symbol: string;
  series: string | null;
  trade_date: string; // ISO date
  expiry_date: string; // ISO date
  reverse_leg_date: string | null; // ISO date
  quantity_lent: number;
  lending_fee: number; // per share (rate, not a money total)
  stored_status: string;
  effective_status: ContractStatus;
  days_remaining: number; // signed: negative once past expiry
  tenure_days: number;
  lending_fee_income: number;
  rollover_history?: {id:number;series_code:string|null;trade_date:string;settlement_date:string;status:string;quantity:number}[];
  estimated_charges?: number | null;
  net_income?: number | null; // quantity_lent * lending_fee, backend-rounded (INR)
  is_rollover_candidate: boolean;
  notes: string | null;
  /** Phase 4: roll chain; chain_days = settlement − origin_date (SEBI cap 365). */
  origin_date?: string | null;
  rolled_from_id?: number | null;
  series_code?: string | null;
  closed_on?: string | null;
  chain_days?: number | null;
  /**
   * Set only on the create response: the contract was traded before the last
   * holdings import, so it was accepted without the in-demat check.
   */
  warning?: string | null;
}

export interface ContractCreate {
  stock_id: number;
  expiry_date: string; // ISO date
  quantity_lent: number;
  lending_fee: number;
  series?: string | null;
  trade_date?: string | null;
  reverse_leg_date?: string | null;
  notes?: string | null;
}

// ---------------------------------------------------------------------------
// Rollover Advisor (schemas.py: RolloverRecommendationResponse / RolloverOptionResponse)
//
// Decision-support only. Every figure comes from a PERSISTED market snapshot —
// the backend never fabricates future-series data. `status` is INSUFFICIENT_DATA
// when nothing has been captured for the symbol's later series. The frontend
// only displays; it makes no recommendation of its own.
// ---------------------------------------------------------------------------

export type RolloverStatus = "INSUFFICIENT_DATA" | "ROLL_AVAILABLE" | "BLOCKED_BY_CAP";

export interface RolloverOption {
  series: string | null;
  expiry_date: string; // ISO date
  market_fee: number | null; // per share (snapshot last-traded fee)
  annualized_yield: number | null;
  open_positions: number | null;
  data_as_of: string; // ISO datetime — when the snapshot was captured
  /** Phase 4.4: priced at the Series B bid on the fillable quantity, net of charges. */
  series_code?: string | null;
  best_bid?: number | null;
  best_bid_qty?: number;
  fillable?: number;
  net_fee?: number | null;
  net_yield_pa?: number | null;
  chain_days?: number; // this settlement - origin_date
  within_cap?: boolean; // chain_days <= 365
}

export interface RolloverRecommendation {
  contract_id: number;
  stock_id: number;
  symbol: string;
  current_series: string | null;
  current_expiry: string; // ISO date
  current_fee: number; // per share
  days_remaining: number; // signed
  status: RolloverStatus;
  rationale: string;
  best_option: RolloverOption | null;
  fee_delta: number | null; // best future fee - current fee (per share)
  favorable: boolean | null; // fee_delta >= 0, or null when not comparable
  options: RolloverOption[];
  origin_date?: string | null;
  days_used?: number | null;
  days_used_label?: string | null; // "N of 365 days used"
}

/** POST /api/contracts/{id}/roll body (schemas.py: ContractRoll). */
export interface ContractRollPayload {
  expiry_date: string;
  reverse_leg_date?: string | null;
  lending_fee: number;
  series?: string | null;
  series_code?: string | null;
  quantity_lent?: number;
  trade_date?: string;
  notes?: string | null;
}

// ---------------------------------------------------------------------------
// Alerts (schemas.py: AlertCreate / AlertResponse / AlertEvaluationRunResponse)
//
// An alert is a configured RULE. Evaluation + delivery are backend-owned: the
// frontend creates/toggles/deletes rules and can trigger an evaluation run, but
// never evaluates conditions itself. Notification credentials are never sent to
// or returned from the frontend.
// ---------------------------------------------------------------------------

export type AlertType =
  | "YIELD_THRESHOLD"
  | "ABOVE_AVERAGE"
  | "TIGHT_SPREAD"
  | "ROLLOVER"
  | "EXPIRY_REMINDER"
  | "LENDING_OPPORTUNITY";

export interface Alert {
  id: number;
  stock_id: number | null;
  alert_type: AlertType;
  threshold: number | null;
  is_active: boolean;
  is_triggered: boolean;
  last_triggered_at: string | null; // ISO datetime
  trigger_message: string | null;
  created_at: string; // ISO datetime
}

export interface AlertCreate {
  alert_type: AlertType;
  threshold?: number | null;
  stock_id?: number | null;
  is_active?: boolean;
}

export interface AlertEvaluationResult {
  alert_id: number;
  alert_type: AlertType;
  stock_id: number | null;
  threshold_used: number;
  condition_met: boolean;
  fired_now: boolean;
  message: string | null;
}

export interface AlertDeliveryResult {
  alert_id: number;
  channel: string;
  delivered: boolean;
  detail: string;
}

export interface AlertEvaluationRun {
  evaluated: number;
  fired: number;
  results: AlertEvaluationResult[];
  deliveries: AlertDeliveryResult[];
}

// Persisted delivery-audit row (schemas.py: NotificationDeliveryResponse).
// Outcome data only — never any SMTP credential.
export interface NotificationDelivery {
  id: number;
  alert_id: number | null; // nulled (SET NULL) if the source rule is deleted
  channel: string; // "in_app" | "email"
  delivered: boolean;
  detail: string | null;
  created_at: string; // ISO datetime (naive-UTC, no trailing "Z")
}

// Envelope for GET /api/alerts/deliveries (schemas.py: AlertDeliveryLogResponse).
export interface AlertDeliveryLog {
  email_channel_active: boolean; // non-secret: is the SMTP channel configured?
  deliveries: NotificationDelivery[];
}

// ---------------------------------------------------------------------------
// Real-time push (Phase 4 WebSocket — /api/ws)
//
// Frames the backend pushes over the live socket. These mirror the envelopes
// emitted by AlertSchedulerService.build_alert_fired_message / run_one_tick and
// the connection ack. The socket carries backend-computed payloads only; the UI
// treats an event as a signal to REST-refetch authoritative data, never as a
// source of truth to render or recompute from.
// ---------------------------------------------------------------------------

export interface ConnectedEvent {
  type: "connected";
  at: string; // ISO datetime (naive-UTC, no trailing "Z")
}

export interface AlertFiredEvent {
  type: "alert_fired";
  alert_id: number;
  alert_type: string;
  stock_id: number | null;
  message: string | null;
  last_triggered_at: string | null; // ISO datetime
}

export interface SchedulerTickEvent {
  type: "scheduler_tick";
  evaluated: number;
  fired: number;
  market_refreshed: number;
  at: string; // ISO datetime
}

export type LiveAlertEvent = ConnectedEvent | AlertFiredEvent | SchedulerTickEvent;

// ---------------------------------------------------------------------------
// P&L (schemas.py: PnLPreviewRequest / PnLBreakdownResponse / PnLRecordResponse
// / PnLListResponse). All money math is backend-owned (Decimal). The tax figure
// is a clearly-labeled configurable ESTIMATE, never an actual liability.
// ---------------------------------------------------------------------------

export interface PnLBreakdown {
  quantity_lent: number;
  lending_fee: number;
  processing_fee_pct: number;
  gst_pct: number;
  /** Flat DP charge per scrip in INR, before GST (dp_charge is GST-inclusive). */
  dp_charge_flat_inr: number;
  tax_slab_pct: number;
  gross_fee: number;
  processing_charge: number;
  gst: number;
  dp_charge: number;
  net_income: number;
  estimated_tax: number;
  post_tax_income: number;
}

export interface PnLRecord {
  id: number;
  contract_id: number;
  symbol: string;
  series: string | null;
  gross_fee: number;
  processing_charge: number;
  gst: number;
  dp_charge: number;
  net_income: number;
  tax_slab_pct: number;
  estimated_tax: number;
  post_tax_income: number;
  created_at: string; // ISO datetime
}

export interface PnLTotals {
  records: number;
  gross_fee: number;
  processing_charge: number;
  gst: number;
  dp_charge: number;
  net_income: number;
  estimated_tax: number;
  post_tax_income: number;
}

export interface PnLList {
  totals: PnLTotals;
  records: PnLRecord[];
}

// ---------------------------------------------------------------------------
// Analytics (schemas.py: AnalyticsSummaryResponse and nested models)
//
// Read-only roll-ups over persisted data. Backend-computed; the frontend only
// renders. No forecasts.
// ---------------------------------------------------------------------------

export interface FinancialYearIncome {
  financial_year: string;
  contracts: number;
  gross_fee: number;
  net_income: number;
  estimated_tax: number;
  post_tax_income: number;
}

export interface SymbolYield {
  symbol: string;
  latest_yield: number | null;
  average_yield: number | null;
  samples: number;
}

export interface AnalyticsSummary {
  generated_at: string; // ISO datetime
  status_breakdown: Record<string, number>;
  income_by_financial_year: FinancialYearIncome[];
  yield_by_symbol: SymbolYield[];
}

// ---------------------------------------------------------------------------
// Historical trends (Phase 5B — schemas.py: SLBHistory* / Equity* )
//
// Two independent histories, both read-only and backend-computed:
//  * SLB snapshot trend — one aggregated point per calendar day (mean of that
//    day's captured snapshots), per-symbol or across the owned portfolio. The
//    window is clamped server-side to the snapshot-retention horizon.
//  * Equity price/volume history — persisted daily OHLC/volume bars for an owned
//    symbol, plus DESCRIPTIVE-only statistics (period high/low, period return,
//    average volume, sample stddev of daily returns). No forecasting; absent
//    source fields stay null (never fabricated as 0).
// ---------------------------------------------------------------------------

export interface SLBHistoryPoint {
  date: string; // ISO date
  annualized_yield: number | null;
  spread: number | null; // ask - bid, per share (INR)
  open_positions: number | null;
  samples: number; // snapshots collapsed into this day
}

export interface SLBHistory {
  scope: "symbol" | "portfolio";
  symbol: string | null; // set when scope === "symbol"
  days: number; // effective window after any server-side clamp
  points: SLBHistoryPoint[];
}

export interface EquityBar {
  symbol: string;
  date: string; // ISO date
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  vwap: number | null;
  volume: number | null;
  delivery_qty: number | null;
  delivery_pct: number | null;
}

export interface EquityStats {
  symbol: string;
  samples: number;
  period_start: string | null; // ISO date
  period_end: string | null; // ISO date
  first_close: number | null;
  last_close: number | null;
  period_high: number | null;
  period_low: number | null;
  period_return_pct: number | null; // (last-first)/first * 100
  avg_volume: number | null;
  daily_return_volatility_pct: number | null; // sample stddev of daily returns
}

export interface EquityHistory {
  symbol: string;
  days: number;
  bars: EquityBar[];
  stats: EquityStats;
}

export interface EquityRefreshResult {
  refreshed: Record<string, number>; // symbol -> bars upserted
  symbols: number;
  total_bars: number;
}

// ---------------------------------------------------------------------------
// Settings (schemas.py: SettingsResponse / SettingsUpdate)
//
// Editable (Phase 5D), non-secret business configuration driving the engines.
// Overrides layer over the environment/.env base. Secrets / credentials are
// NEVER exposed to, or accepted from, the frontend.
// ---------------------------------------------------------------------------

export interface BusinessSettings {
  expiring_soon_days: number;
  default_alert_yield_threshold: number;
  tight_spread_threshold_pct: number;
  above_average_yield_margin_pct: number;
  notifications_enabled: boolean;
  /** Per-rule cooldown after a fire, minutes (Phase 5.4); 0 = off. */
  alert_cooldown_minutes: number;
  /** Desktop notifications on this Mac (Phase 5.1). */
  macos_notifications_enabled: boolean;
  /** Live board: seconds between full sweeps in market hours (30–3600). */
  live_refresh_seconds: number;
  /** Opportunities (Phase 3.4): net yield floor %, smallest fill, expiries shown. */
  min_net_yield_pct: number;
  min_fill_qty: number;
  /** Smallest net fee (INR) worth acting on; 0 = off. */
  min_net_fee_inr: number;
  tracked_expiries: number;
  /** ROLLOVER alert: days before the reverse leg to warn (Phase 4.5). */
  rollover_warn_days: number;
  broker_processing_fee_pct: number;
  gst_pct: number;
  dp_charge_flat_inr: number;
  default_tax_slab_pct: number;
}

/**
 * Partial update payload for PUT /api/settings (Phase 5D, schemas.py:
 * SettingsUpdate). Every field is optional — only the keys sent are changed,
 * and the rest keep their persisted/env value. Secrets are not part of
 * BusinessSettings, so they can never appear here.
 */
export type BusinessSettingsUpdate = Partial<BusinessSettings>;

// ---------------------------------------------------------------------------
// Dashboard (schemas.py: DashboardSummaryResponse and nested models)
// ---------------------------------------------------------------------------

export interface PortfolioSummary {
  total_stocks: number;
  slb_eligible_stocks: number;
  /** Imported quantity (Zerodha's export leaves lent shares out). */
  total_quantity: number;
  in_demat: number;
  lent: number;
  /** in_demat + lent: shown as "shares held". */
  total_owned: number;
  portfolio_cost_value: number;
}

export interface ContractsSummary {
  active: number;
  expiring_soon: number;
  expired: number;
}

export interface IncomeSummary {
  total_gross_fee: number;
  total_net_income: number;
  current_fy_net_income: number;
  financial_year: string;
}

/** Shares out on loan under ACTIVE contracts (schemas.py: LentOutSummary). */
export interface LentOutSummary {
  stocks: number;
  shares: number;
  value_at_last_price: number;
  /** Lent stocks with no known price (not included in the value). */
  unpriced_symbols: string[];
  fee_income_due_gross: number;
  /** Net of processing, GST and the flat DP charge. */
  fee_income_due_net: number;
}

export interface UpcomingExpiryItem {
  contract_id: number;
  symbol: string;
  series: string | null;
  expiry_date: string;
  quantity_lent: number;
  lending_fee: number;
  days_remaining: number;
}

export interface BestOpportunityItem {
  symbol: string;
  series: string | null;
  expiry_date: string | null;
  ltp: number;
  best_bid: number;
  best_ask: number;
  /** NET annualised yield at the live Series B bid (Phase 3.5). */
  annualized_yield: number;
  open_positions: number;
  timestamp: string;
  fillable?: number;
  net_fee?: number;
  days_to_settlement?: number;
  ticket?: string | null;
}

export interface RecentAlertItem {
  id: number;
  stock_id: number | null;
  alert_type: string;
  threshold: number | null;
  message: string | null;
  triggered_at: string | null;
}

export interface DashboardSummary {
  generated_at: string;
  portfolio: PortfolioSummary;
  contracts: ContractsSummary;
  income: IncomeSummary;
  lent_out: LentOutSummary;
  upcoming_expiries: UpcomingExpiryItem[];
  best_opportunities: BestOpportunityItem[];
  recent_alerts: RecentAlertItem[];
}

// ---------------------------------------------------------------------------
// Health (schemas.py: HealthResponse)
// ---------------------------------------------------------------------------

export interface HealthResponse {
  status: string;
  version: string;
  database: string;
  timestamp: string;
}

// ---------------------------------------------------------------------------
// Live board (Phase 2.13/2.14; routes/portfolio.py: /api/portfolio/live and
// /api/portfolio/live/trades). "Trades" are INFERRED from rises in a series'
// traded quantity between sweeps: NSE publishes no SLB trade tape.
// ---------------------------------------------------------------------------

export type SeriesFamily = "A" | "B" | "SPECIAL";

export interface LiveSeries {
  fee_from_previous_observation?: boolean;
  fee_observed_at?: string;
  is_stale?: boolean;
  underlying_updated_at?: string;
  underlying_source?: string;
  underlying_price?: number | null;
  fillable?: number;
  net_fee?: number | null;
  net_yield_pa?: number | null;
  series_code: string;
  series_family: SeriesFamily | null;
  series: string | null; // month label, e.g. "Nov-2026"
  settlement_date: string; // ISO date
  settlement_estimated: boolean | null;
  ltp: number | null; // last traded fee
  traded_quantity: number | null; // today's cumulative
  open_positions: number | null;
  best_bid: number | null;
  best_bid_qty: number | null;
  best_ask: number | null;
  best_ask_qty: number | null;
  annualized_yield: number | null;
  updated_at: string;
  age_seconds: number;
}

export type LiveHoldingStatus = "live" | "no_data" | "not_on_slb";

export interface LiveHolding {
  name?: string | null;
  lendable?: number;
  stock_id: number;
  symbol: string;
  total_owned: number;
  in_demat: number;
  lent: number;
  status: LiveHoldingStatus;
  slb_checked_at: string | null;
  series: LiveSeries[];
}

export interface LiveSweepStatus {
  at: string;
  symbols: number;
  fetched: number;
  duration_seconds: number;
  refresh_seconds: number;
  warning: string | null;
}

export interface LiveBoard {
  generated_at: string;
  market_open: boolean;
  refresh_seconds: number;
  series_family: string;
  last_sweep: LiveSweepStatus | null;
  sweep_warning: string | null;
  holdings: LiveHolding[];
}

export interface TradeEvent {
  id?: number;
  inferred: true;
  symbol: string;
  series_code: string;
  series_family: SeriesFamily | null;
  series: string | null;
  settlement_date: string | null;
  quantity: number;
  last_fee: number | null;
  traded_quantity_total: number;
  occurred_at: string;
}

export interface TradeFeed {
  since: string;
  inferred: true;
  events: TradeEvent[];
}

/** `market.trade` frame on /api/ws. */
export interface MarketTradeMessage extends TradeEvent {
  type: "market.trade";
}

// ---------------------------------------------------------------------------
// Per-stock SLB quote and NSE history (Phase 6.3; routes/slb.py:
// /api/slb/quote/{symbol} and /api/slb/quote/{symbol}/history).
// ---------------------------------------------------------------------------

export interface DepthLevel {
  price: number;
  quantity: number;
}

export interface QuoteSeries {
  symbol?: string;
  series_code: string | null; // null on symbol-view / cached legacy rows
  series_family: SeriesFamily | null;
  month: string | null;
  settlement_date: string | null;
  settlement_estimated: boolean | null;
  bids?: DepthLevel[];
  offers?: DepthLevel[];
  best_bid: number;
  best_bid_qty: number;
  best_ask: number;
  best_ask_qty: number;
  ltp: number;
  traded_quantity: number;
  open_positions: number;
  underlying_price: number;
  annualized_yield: number;
  corporate_action: { purpose: string | null; ex_date?: string | null } | null;
  is_active: boolean;
  as_of?: string | null; // cached / symbol-view rows: when stored
}

export interface SLBQuote {
  symbol: string;
  /** quote = live NSE quote API; symbol_view = fallback feed; cache = stored rows (NSE failed or blocked). */
  source: "quote" | "symbol_view" | "cache";
  blocked: boolean;
  as_of: string | null; // NSE's own timestamp for a live quote
  company_name?: string | null;
  series: QuoteSeries[];
}

export interface NSEHistoryRow {
  trade_date: string;
  settlement_date: string | null;
  series_code: string;
  series_family: SeriesFamily;
  open: number;
  high: number;
  low: number;
  close: number;
  previous_close: number;
  quantity: number;
  trades: number;
  value: number;
}

export interface NSEHistory {
  symbol: string;
  from: string;
  to: string;
  rows: NSEHistoryRow[];
}

// ---------------------------------------------------------------------------
// Lending opportunities (Phase 3.5; routes/opportunities.py). Priced at the
// live Series B bid on the fillable quantity, net of Zerodha's charges.
// ---------------------------------------------------------------------------

export interface OpportunityOption {
  series_code: string;
  series: string | null;
  settlement_date: string;
  settlement_estimated: boolean | null;
  days: number;
  days_to_settlement: number;
  best_bid: number;
  best_bid_qty: number;
  best_ask: number | null;
  spot: number | null;
  lendable: number;
  fillable: number;
  gross_fee: number | null;
  net_fee: number | null;
  gross_yield_pa: number | null;
  net_yield_pa: number | null;
  breakeven_price: number | null;
  price_for_floor: number | null;
  corporate_action: string | null;
  quote_at: string;
  quote_age_seconds: number;
  actionable: boolean;
  reasons: string[];
  ticket: string | null;
}

export interface OpportunityStock {
  symbol: string;
  stock_id: number;
  in_demat: number;
  pledged: number;
  lendable: number;
  options: OpportunityOption[];
}

export interface OpportunityBoard {
  generated_at: string;
  expiries: number;
  min_net_yield_pct: number;
  min_fill_qty: number;
  min_net_fee_inr: number;
  series_family: string;
  stocks: OpportunityStock[];
}
