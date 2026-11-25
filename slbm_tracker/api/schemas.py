"""Pydantic schemas for API request validation and response serialization."""

from __future__ import annotations

import datetime
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field


class StockBase(BaseModel):
    """Base attributes for a stock."""

    symbol: str = Field(..., min_length=1, max_length=25, description="Stock ticker symbol (e.g. RELIANCE)")
    name: Optional[str] = Field(None, max_length=200, description="Company name")
    isin: Optional[str] = Field(None, max_length=20, description="ISIN code")
    quantity: int = Field(0, ge=0, description="Holding quantity")
    avg_buy_price: float = Field(0.0, ge=0.0, description="Average purchase price")
    is_slb_eligible: bool = Field(True, description="Whether the stock is eligible for SLB")
    notes: Optional[str] = Field(None, description="Optional user notes")


class StockCreate(StockBase):
    """Payload for adding a new stock or updating existing."""


class StockUpdate(BaseModel):
    """Payload for updating an existing stock holding."""

    name: Optional[str] = None
    isin: Optional[str] = None
    quantity: Optional[int] = Field(None, ge=0)
    avg_buy_price: Optional[float] = Field(None, ge=0.0)
    is_slb_eligible: Optional[bool] = None
    notes: Optional[str] = None


class StockResponse(StockBase):
    """Serialized stock record."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime.datetime
    updated_at: datetime.datetime
    holdings_imported_at: Optional[datetime.datetime] = None
    pledged_quantity: int = 0


class LentContractItem(BaseModel):
    """An ACTIVE contract shown as a Lent badge on the holdings page."""

    contract_id: int
    series: Optional[str]
    quantity_lent: int
    trade_date: datetime.date
    # reverse_leg_date when recorded, else expiry_date.
    settlement_date: datetime.date


class HoldingResponse(StockResponse):
    """A stock with where its shares are (house rule 5).

    ``quantity`` is what the last import (or manual edit) said; Zerodha's
    export leaves lent shares out, so in demat / lent / total owned come from
    :class:`~slbm_tracker.services.portfolio_service.HoldingPosition`.
    """

    in_demat: int
    lent: int
    total_owned: int
    # in_demat − pledged: what can be lent now (Phase 3.2).
    lendable: int = 0
    lent_contracts: list[LentContractItem] = Field(default_factory=list)


class PortfolioImportResponse(BaseModel):
    """Response payload after importing a holdings CSV."""

    total_parsed: int
    added: int
    updated: int
    errors: list[str] = Field(default_factory=list)
    stocks: list[StockResponse] = Field(default_factory=list)
    # Stocks missing from the file, set to quantity 0 (never deleted).
    zeroed: list[str] = Field(default_factory=list)
    # Why zeroing was skipped (no holdings parsed, or unreadable rows); None if it ran.
    zeroing_skipped: Optional[str] = None


class TrackedSymbolResponse(BaseModel):
    """A symbol in the configured/seeded SLB tracking universe.

    Deliberately carries no holdings or market data — only the ticker. This is
    the symbol universe used for selection and monitoring, not portfolio
    ownership (see StockResponse for holdings).
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    symbol: str
    created_at: datetime.datetime


class SymbolUniverseResponse(BaseModel):
    """The full tracked-symbol universe available to the frontend."""

    count: int
    symbols: list[TrackedSymbolResponse] = Field(default_factory=list)


class SLBQuoteItem(BaseModel):
    """Market quote item for an SLB contract series."""

    symbol: str
    series: str
    expiry_date: Optional[datetime.date] = None
    best_bid: float = 0.0
    best_bid_qty: int = 0
    best_ask: float = 0.0
    best_ask_qty: int = 0
    ltp: float = 0.0
    underlying_price: float = 0.0
    spread: float = 0.0
    spread_pct: float = 0.0
    open_positions: int = 0
    volume: int = 0
    turnover: float = 0.0
    annualized_yield: float = 0.0
    corporate_action: Optional[str] = None
    is_slb_eligible: bool = True
    timestamp: datetime.datetime


class SLBLiveResponse(BaseModel):
    """Response payload for symbol SLB contracts."""

    symbol: str
    contracts_count: int
    contracts: list[SLBQuoteItem]


class ContractCreate(BaseModel):
    """Payload to open a lending contract against an existing portfolio stock."""

    stock_id: int = Field(..., description="ID of the portfolio Stock being lent")
    expiry_date: datetime.date = Field(..., description="First-Tuesday SLB expiry date")
    quantity_lent: int = Field(..., gt=0, description="Number of shares lent out")
    lending_fee: float = Field(..., ge=0.0, description="Lending fee per share (INR)")
    series: Optional[str] = Field(None, max_length=10, description="SLB series, e.g. 'Oct-2026'")
    trade_date: Optional[datetime.date] = Field(None, description="Trade date; defaults to today (IST)")
    reverse_leg_date: Optional[datetime.date] = None
    notes: Optional[str] = None
    series_code: Optional[str] = Field(None, max_length=10, description="Series code, e.g. 'XN'")


class ContractRoll(BaseModel):
    """Roll an open contract into a later series (Phase 4.3)."""

    model_config = ConfigDict(extra="forbid")

    expiry_date: datetime.date
    lending_fee: float = Field(..., ge=0.0, description="New fee per share (INR)")
    series: Optional[str] = Field(None, max_length=10)
    series_code: Optional[str] = Field(None, max_length=10)
    reverse_leg_date: Optional[datetime.date] = None
    quantity_lent: Optional[int] = Field(None, gt=0, description="Default: the same quantity")
    trade_date: Optional[datetime.date] = Field(None, description="Default: today (IST)")
    notes: Optional[str] = None


class ContractUpdate(BaseModel):
    """Corrections to a contract (PATCH). Status changes use the actions."""

    model_config = ConfigDict(extra="forbid")

    notes: Optional[str] = None
    lending_fee: Optional[float] = Field(None, ge=0.0)
    series: Optional[str] = Field(None, max_length=10)
    series_code: Optional[str] = Field(None, max_length=10)
    reverse_leg_date: Optional[datetime.date] = None
    expiry_date: Optional[datetime.date] = None
    trade_date: Optional[datetime.date] = None


class ContractResponse(BaseModel):
    """A lending contract with derived, date-consistent lifecycle fields.

    ``effective_status`` (ACTIVE / EXPIRING_SOON / EXPIRED or a terminal
    business state) is derived from the expiry date vs. today in IST and never
    drifts from the dates. ``lending_fee_income`` is quantity × per-share fee.
    """

    id: int
    stock_id: int
    symbol: str
    series: Optional[str] = None
    trade_date: datetime.date
    expiry_date: datetime.date
    reverse_leg_date: Optional[datetime.date] = None
    quantity_lent: int
    lending_fee: float
    stored_status: str
    effective_status: str
    days_remaining: int
    tenure_days: int
    lending_fee_income: float
    rollover_history: list[dict[str, Any]] = Field(default_factory=list)
    estimated_charges: Optional[float] = None
    net_income: Optional[float] = None
    is_rollover_candidate: bool
    notes: Optional[str] = None
    # Phase 4: roll chain. chain_days = settlement - origin_date (SEBI cap 365).
    origin_date: Optional[datetime.date] = None
    rolled_from_id: Optional[int] = None
    series_code: Optional[str] = None
    closed_on: Optional[datetime.date] = None
    chain_days: Optional[int] = None
    # Set on create only: the contract was traded before the last holdings
    # import, so it was accepted without the in-demat check.
    warning: Optional[str] = None


class RolloverOptionResponse(BaseModel):
    """A future series a holding could roll into — from persisted market data only."""

    series: Optional[str] = None
    expiry_date: datetime.date
    market_fee: Optional[float] = None  # per share (snapshot last-traded fee)
    annualized_yield: Optional[float] = None
    open_positions: Optional[int] = None
    data_as_of: datetime.datetime  # when the snapshot was captured
    # Phase 4.4: priced at the Series B bid on the fillable quantity, net of charges.
    series_code: Optional[str] = None
    best_bid: Optional[float] = None
    best_bid_qty: int = 0
    fillable: int = 0
    net_fee: Optional[float] = None
    net_yield_pa: Optional[float] = None
    chain_days: int = 0
    within_cap: bool = True


class RolloverRecommendationResponse(BaseModel):
    """Decision-support summary for one rollover-candidate contract.

    ``status`` is ``INSUFFICIENT_DATA`` when no future-series market data has
    been captured, or ``ROLL_AVAILABLE`` when at least one later series has a
    persisted snapshot. ``favorable`` is a purely factual comparison of the best
    available future fee vs the current fee (per share); it is ``null`` when the
    two are not comparable. Nothing here executes or simulates an order.
    """

    contract_id: int
    stock_id: int
    symbol: str
    current_series: Optional[str] = None
    current_expiry: datetime.date
    current_fee: float
    days_remaining: int
    status: str
    rationale: str
    best_option: Optional[RolloverOptionResponse] = None
    fee_delta: Optional[float] = None
    favorable: Optional[bool] = None
    options: list[RolloverOptionResponse] = Field(default_factory=list)
    # Phase 4.4: SEBI's 365-day cap. status may also be BLOCKED_BY_CAP.
    origin_date: Optional[datetime.date] = None
    days_used: Optional[int] = None
    days_used_label: Optional[str] = None


class AlertCreate(BaseModel):
    """Payload to create an alert RULE.

    ``threshold`` is optional; when omitted the per-type config default is used
    (e.g. yield threshold, tight-spread%%, expiry-reminder days). ``stock_id``
    scopes the rule to one holding; omit for a portfolio-wide rule.
    """

    alert_type: str = Field(
        ...,
        description="YIELD_THRESHOLD (gross) | ABOVE_AVERAGE | TIGHT_SPREAD | ROLLOVER | EXPIRY_REMINDER | LENDING_OPPORTUNITY (net, at the bid)",
    )
    threshold: Optional[float] = Field(None, description="Rule threshold; defaults per type if unset")
    stock_id: Optional[int] = Field(None, description="Scope to one holding; omit for portfolio-wide")
    is_active: bool = Field(True, description="Whether the rule is evaluated")


class AlertUpdate(BaseModel):
    """Payload to enable/disable an alert rule."""

    is_active: bool


class AlertResponse(BaseModel):
    """A configured alert rule with its latest trigger state."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    stock_id: Optional[int] = None
    alert_type: str
    threshold: Optional[float] = None
    is_active: bool
    is_triggered: bool
    last_triggered_at: Optional[datetime.datetime] = None
    trigger_message: Optional[str] = None
    created_at: datetime.datetime


class AlertEvaluationResult(BaseModel):
    """Outcome of evaluating one alert rule in a run."""

    alert_id: int
    alert_type: str
    stock_id: Optional[int] = None
    threshold_used: float
    condition_met: bool
    fired_now: bool
    message: Optional[str] = None


class AlertDeliveryResultResponse(BaseModel):
    """Outcome of attempting to deliver one triggered alert on one channel."""

    alert_id: Optional[int] = None
    channel: str
    delivered: bool
    detail: str
    outcome: Optional[str] = None  # sent | skipped | failed (Phase 5)


class AlertEvaluationRunResponse(BaseModel):
    """Result of an evaluate (+optional deliver) run."""

    evaluated: int
    fired: int
    results: list[AlertEvaluationResult] = Field(default_factory=list)
    deliveries: list[AlertDeliveryResultResponse] = Field(default_factory=list)


class NotificationDeliveryResponse(BaseModel):
    """One persisted delivery-audit row (Phase 5A). No secrets — outcome only."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    alert_id: Optional[int] = None
    channel: str
    delivered: bool
    detail: Optional[str] = None
    outcome: Optional[str] = None  # sent | skipped | failed (Phase 5)
    created_at: datetime.datetime


class AlertDeliveryLogResponse(BaseModel):
    """Recent delivery-audit log plus a non-secret email-channel status flag."""

    email_channel_active: bool
    deliveries: list[NotificationDeliveryResponse] = Field(default_factory=list)


class PnLPreviewRequest(BaseModel):
    """Inputs for a P&L breakdown preview (no persistence)."""

    quantity_lent: int = Field(..., ge=0, description="Shares lent")
    lending_fee: float = Field(..., ge=0.0, description="Lending fee per share (INR)")
    tax_slab_pct: Optional[float] = Field(
        None, ge=0.0, le=100.0, description="Override tax-slab %% for the ESTIMATE"
    )


class PnLBreakdownResponse(BaseModel):
    """A fully-itemized lending P&L breakdown (INR, 2dp). All rates are echoed."""

    quantity_lent: int
    lending_fee: float
    processing_fee_pct: float
    gst_pct: float
    dp_charge_flat_inr: float
    tax_slab_pct: float
    gross_fee: float
    processing_charge: float
    gst: float
    dp_charge: float
    net_income: float
    estimated_tax: float
    post_tax_income: float


class PnLRecordResponse(BaseModel):
    """A persisted P&L record for one contract, with its symbol/series."""

    id: int
    contract_id: int
    symbol: str
    series: Optional[str] = None
    gross_fee: float
    processing_charge: float
    gst: float
    dp_charge: float
    net_income: float
    tax_slab_pct: float
    estimated_tax: float
    post_tax_income: float
    created_at: datetime.datetime


class PnLTotals(BaseModel):
    """Summed P&L across records."""

    records: int = 0
    gross_fee: float = 0.0
    processing_charge: float = 0.0
    gst: float = 0.0
    dp_charge: float = 0.0
    net_income: float = 0.0
    estimated_tax: float = 0.0
    post_tax_income: float = 0.0


class PnLListResponse(BaseModel):
    """All P&L records plus their totals."""

    totals: PnLTotals
    records: list[PnLRecordResponse] = Field(default_factory=list)


class FinancialYearIncomeResponse(BaseModel):
    """P&L totals for one Indian financial year."""

    financial_year: str
    contracts: int
    gross_fee: float
    net_income: float
    estimated_tax: float
    post_tax_income: float


class SymbolYieldResponse(BaseModel):
    """Yield stats for one symbol (from persisted snapshots)."""

    symbol: str
    latest_yield: Optional[float] = None
    average_yield: Optional[float] = None
    samples: int


class AnalyticsSummaryResponse(BaseModel):
    """Combined analytics payload (read-only roll-ups over persisted data)."""

    generated_at: datetime.datetime
    status_breakdown: dict[str, int] = Field(default_factory=dict)
    income_by_financial_year: list[FinancialYearIncomeResponse] = Field(default_factory=list)
    yield_by_symbol: list[SymbolYieldResponse] = Field(default_factory=list)


# --- Phase 5B: historical trends (SLB snapshots) & equity price/volume history ---


class SLBHistoryPointResponse(BaseModel):
    """One calendar day's aggregated SLB metrics (mean of that day's snapshots)."""

    date: str  # ISO date
    annualized_yield: Optional[float] = None
    spread: Optional[float] = None
    open_positions: Optional[int] = None
    samples: int


class SLBHistoryResponse(BaseModel):
    """Daily SLB trend for one symbol or the owned portfolio, over a day window."""

    scope: str  # "symbol" | "portfolio"
    symbol: Optional[str] = None  # set when scope == "symbol"
    days: int  # effective window after any server-side clamp
    points: list[SLBHistoryPointResponse] = Field(default_factory=list)


class EquityBarResponse(BaseModel):
    """One persisted daily equity price/volume bar. Missing fields stay null."""

    model_config = ConfigDict(from_attributes=True)

    symbol: str
    date: datetime.date
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    vwap: Optional[float] = None
    volume: Optional[int] = None
    delivery_qty: Optional[int] = None
    delivery_pct: Optional[float] = None


class EquityStatsResponse(BaseModel):
    """Descriptive-only statistics over the stored equity window (no forecasting)."""

    symbol: str
    samples: int
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    first_close: Optional[float] = None
    last_close: Optional[float] = None
    period_high: Optional[float] = None
    period_low: Optional[float] = None
    period_return_pct: Optional[float] = None
    avg_volume: Optional[int] = None
    daily_return_volatility_pct: Optional[float] = None


class EquityHistoryResponse(BaseModel):
    """Persisted equity price/volume history plus descriptive stats for a symbol."""

    symbol: str
    days: int
    bars: list[EquityBarResponse] = Field(default_factory=list)
    stats: EquityStatsResponse


class EquityRefreshResponse(BaseModel):
    """Outcome of a NSE equity-history refresh (bars upserted per symbol)."""

    refreshed: dict[str, int] = Field(default_factory=dict)
    symbols: int
    total_bars: int


class SettingsResponse(BaseModel):
    """Non-secret business configuration that drives Phase 3 calculations.

    Deliberately excludes any credentials/secrets — none are exposed to the
    frontend. These are the effective values (from environment / .env) used by
    the contract, alert and P&L engines, surfaced read-only so users can see
    exactly what rates and thresholds are being applied.
    """

    # Contracts
    expiring_soon_days: int
    # Alerts
    default_alert_yield_threshold: float
    tight_spread_threshold_pct: float
    above_average_yield_margin_pct: float
    notifications_enabled: bool
    alert_cooldown_minutes: int
    macos_notifications_enabled: bool
    # Live board
    live_refresh_seconds: int
    # Opportunities
    min_net_yield_pct: float
    min_fill_qty: int
    min_net_fee_inr: float
    tracked_expiries: int
    rollover_warn_days: int
    # P&L
    broker_processing_fee_pct: float
    gst_pct: float
    dp_charge_flat_inr: float
    default_tax_slab_pct: float


class SettingsUpdate(BaseModel):
    """Partial update of the editable non-secret business preferences (Phase 5D).

    Every field is optional so the client may send only what changed; a field
    left unset is not modified. ``extra="forbid"`` rejects any key outside this
    set — SMTP credentials and every other secret are simply not fields here, so
    a request that tries to set one is a 422, never a silent write. Bounds and
    type coercion are enforced centrally by
    :mod:`slbm_tracker.services.business_config` (the single source of truth), so
    these fields carry no duplicated ``Field`` constraints that could drift.
    """

    model_config = ConfigDict(extra="forbid")

    # Contracts
    expiring_soon_days: Optional[int] = None
    # Alerts
    default_alert_yield_threshold: Optional[float] = None
    tight_spread_threshold_pct: Optional[float] = None
    above_average_yield_margin_pct: Optional[float] = None
    notifications_enabled: Optional[bool] = None
    alert_cooldown_minutes: Optional[int] = None
    macos_notifications_enabled: Optional[bool] = None
    # Live board
    live_refresh_seconds: Optional[int] = None
    # Opportunities
    min_net_yield_pct: Optional[float] = None
    min_fill_qty: Optional[int] = None
    min_net_fee_inr: Optional[float] = None
    tracked_expiries: Optional[int] = None
    rollover_warn_days: Optional[int] = None
    # P&L
    broker_processing_fee_pct: Optional[float] = None
    gst_pct: Optional[float] = None
    dp_charge_flat_inr: Optional[float] = None
    default_tax_slab_pct: Optional[float] = None


class HealthResponse(BaseModel):
    """Health check status response."""

    status: str
    version: str
    database: str
    timestamp: datetime.datetime


class PortfolioSummary(BaseModel):
    """Aggregate portfolio counts for the dashboard."""

    total_stocks: int = 0
    slb_eligible_stocks: int = 0
    total_quantity: int = 0  # imported quantity (lent shares excluded)
    in_demat: int = 0
    lent: int = 0
    total_owned: int = 0  # in_demat + lent: the dashboard's "shares held"
    portfolio_cost_value: float = 0.0


class ContractsSummary(BaseModel):
    """Lending contract status counts for the dashboard."""

    active: int = 0
    expiring_soon: int = 0
    expired: int = 0


class IncomeSummary(BaseModel):
    """Income roll-ups derived from recorded P&L."""

    total_gross_fee: float = 0.0
    total_net_income: float = 0.0
    current_fy_net_income: float = 0.0
    financial_year: str


class LentOutSummary(BaseModel):
    """Shares out on loan under ACTIVE contracts (dashboard Lent out card)."""

    stocks: int = 0
    shares: int = 0
    # Lent shares × last known price; stocks with no price are listed, not guessed.
    value_at_last_price: float = 0.0
    unpriced_symbols: list[str] = Field(default_factory=list)
    fee_income_due_gross: float = 0.0
    # Net of processing, GST and the flat DP charge (PnLService rates).
    fee_income_due_net: float = 0.0


class UpcomingExpiryItem(BaseModel):
    """An active lending contract approaching its expiry."""

    contract_id: int
    symbol: str
    series: Optional[str] = None
    expiry_date: datetime.date
    quantity_lent: int
    lending_fee: float
    days_remaining: int


class BestOpportunityItem(BaseModel):
    """An actionable Series B lending opportunity (Phase 3.5).

    ``annualized_yield`` is the NET yield at the live bid on the fillable
    quantity; ``series`` is the Series B code; ``expiry_date`` the settlement.
    """

    symbol: str
    series: Optional[str] = None
    expiry_date: Optional[datetime.date] = None
    ltp: float = 0.0
    best_bid: float = 0.0
    best_ask: float = 0.0
    annualized_yield: float = 0.0
    open_positions: int = 0
    timestamp: datetime.datetime
    fillable: int = 0
    net_fee: float = 0.0
    days_to_settlement: Optional[int] = None
    ticket: Optional[str] = None


class RecentAlertItem(BaseModel):
    """A recently triggered alert."""

    id: int
    stock_id: Optional[int] = None
    alert_type: str
    threshold: Optional[float] = None
    message: Optional[str] = None
    triggered_at: Optional[datetime.datetime] = None


class DashboardSummaryResponse(BaseModel):
    """Full payload backing the frontend Dashboard view."""

    generated_at: datetime.datetime
    portfolio: PortfolioSummary
    contracts: ContractsSummary
    income: IncomeSummary
    lent_out: LentOutSummary = Field(default_factory=LentOutSummary)
    upcoming_expiries: list[UpcomingExpiryItem] = Field(default_factory=list)
    best_opportunities: list[BestOpportunityItem] = Field(default_factory=list)
    recent_alerts: list[RecentAlertItem] = Field(default_factory=list)
