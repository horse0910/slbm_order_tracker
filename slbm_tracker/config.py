"""Application configuration management using Pydantic Settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# Origins permitted to reach the API from a browser. Shared by the CORS
# middleware (REST) and the /api/ws WebSocket handshake check (Phase 4) so the
# two can never drift. Browsers do NOT apply same-origin policy to WebSocket
# upgrades and CORSMiddleware does not guard them, so the WS route validates the
# Origin header itself against this list; a request with NO Origin (non-browser
# / same-process clients such as the test harness) is allowed. These are the
# dev-server / preview / local origins for this single-user localhost app.
ALLOWED_ORIGINS: tuple[str, ...] = (
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:5180",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5180",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    # Phase 7.1: the backend serves the built app itself on 8010.
    "http://localhost:8010",
    "http://127.0.0.1:8010",
)


class Settings(BaseSettings):
    """Central configuration for SLBM Tracker."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    backups_enabled: bool = Field(default=True, description="Daily database backup after market close (Phase 7.4).")
    backup_dir: str = Field(
        default="~/Library/Application Support/slbm-tracker/backups",
        description="Where daily backups go (one file per IST day).",
    )
    backup_keep: int = Field(default=14, ge=1, description="How many daily backups to keep.")
    frontend_dist_dir: str = Field(
        default=str(Path(__file__).resolve().parent.parent / "frontend" / "dist"),
        description="Built frontend served at / with an index.html fallback (Phase 7.1). Skipped if missing.",
    )
    database_url: str = Field(
        default="sqlite:///./slbm_tracker.db",
        description="SQLAlchemy database connection URL",
    )

    # NSE Endpoints & scraping configurations
    # These URLs are fully configurable so they can be changed without modifying code
    nse_base_url: str = Field(
        default="https://www.nseindia.com",
        description="Base URL for NSE India",
    )
    nse_slb_api_url: str = Field(
        default="https://www.nseindia.com/api/live-analysis-slb",
        description="Internal or primary API endpoint for SLB live market data",
    )
    nse_quote_api_url: str = Field(
        default="https://www.nseindia.com/api/NextApi/apiClient/GetQuoteApi",
        description=(
            "NSE per-stock quote API (functionName=getGetQuoteSLBMData / "
            "getSLBHistoricalData). Internal and undocumented, so configurable."
        ),
    )
    nse_slb_page_url: str = Field(
        default="https://www.nseindia.com/market-data/securities-lending-and-borrowing",
        description="Web page URL for SLB market data used for cookie warming",
    )
    nse_all_reports_url: str = Field(
        default="https://www.nseindia.com/all-reports",
        description="NSE All Reports portal URL for fallback reports",
    )
    nse_request_timeout_seconds: float = Field(
        default=10.0,
        description="Timeout in seconds for external NSE requests",
    )
    nse_max_retries: int = Field(
        default=3,
        description="Maximum retry attempts on transient network or rate-limit failures",
    )
    nse_rate_limit_delay_seconds: float = Field(
        default=2.0,
        description="Delay in seconds between successive NSE requests to avoid 429/bans",
    )
    # --- Phase 5B equity price/volume history (security-wise historical data) ---
    # Kept configurable like the other NSE URLs because NSE's exact historical
    # path changes over time and the sandbox cannot confirm it live — the real
    # value is verified against the live site at run time.
    nse_historical_url: str = Field(
        default="https://www.nseindia.com/api/historical/cm/equity",
        description="NSE equity security-wise historical-data endpoint (Phase 5B).",
    )
    equity_history_days: int = Field(
        default=365,
        ge=1,
        le=1825,
        description="Default lookback window (days) when fetching equity price/volume history.",
    )
    nse_history_chunk_days: int = Field(
        default=90,
        ge=1,
        le=365,
        description=(
            "Max span (days) requested from NSE per historical call. NSE caps the "
            "date range per request, so a long window is fetched in adjacent chunks "
            "and merged (deduped on date). Reuses nse_rate_limit_delay_seconds "
            "between chunks."
        ),
    )

    # Financial & calculation defaults
    default_alert_yield_threshold: float = Field(
        default=8.0,
        description="Minimum annualized yield percentage to trigger alerts",
    )
    broker_processing_fee_pct: float = Field(
        default=20.0,
        description="Broker processing fee as percentage of gross lending fee (e.g. 20% for Zerodha)",
    )
    gst_pct: float = Field(
        default=18.0,
        description="GST percentage applied on broker processing fee (18%)",
    )
    dp_charge_flat_inr: float = Field(
        default=13.0,
        ge=0.0,
        description="Flat DP charge per scrip (INR, before GST). GST at gst_pct is added on top: 13 + 18% = 15.34.",
    )

    # --- Phase 3 business preferences (contracts / alerts / P&L / rollover) ---
    expiring_soon_days: int = Field(
        default=7,
        ge=1,
        le=60,
        description="Contracts within this many days of expiry are flagged 'expiring soon'",
    )
    tight_spread_threshold_pct: float = Field(
        default=5.0,
        ge=0.0,
        description="Spread%% at or below this is considered a 'tight spread' alert",
    )
    above_average_yield_margin_pct: float = Field(
        default=20.0,
        ge=0.0,
        description="How far above the trailing average yield (%% of the average) counts as an above-average opportunity",
    )
    above_average_lookback_samples: int = Field(
        default=20,
        ge=2,
        description=(
            "ABOVE_AVERAGE compares the latest yield against the mean of the most "
            "recent N snapshots for the symbol — a TRUE trailing window. Bounding "
            "the sample set is what makes the average 'trailing': without it, "
            "Phase 4's continuous auto-ingestion would average over all history "
            "forever, so the mean would only creep and the rule would fire "
            "progressively less over time. Sample-count based, so the wall-clock "
            "span it covers scales with alert_eval_interval_seconds."
        ),
    )
    default_tax_slab_pct: float = Field(
        default=30.0,
        ge=0.0,
        le=100.0,
        description="Default income-tax slab %% used ONLY for the clearly-labeled P&L tax ESTIMATE",
    )
    notifications_enabled: bool = Field(
        default=True,
        description="Master switch for alert notification delivery (in-app). Delivery is separate from alert evaluation.",
    )

    # --- Phase 4 background alert scheduler / real-time push ---
    # The scheduler only ORCHESTRATES the existing evaluate->deliver chain on an
    # interval; it adds no new calculation. All fields are env-overridable.
    alert_scheduler_enabled: bool = Field(
        default=True,
        description="Master switch for the background alert-evaluation loop. When False the app still serves the WebSocket and manual /alerts/evaluate.",
    )
    alert_eval_interval_seconds: int = Field(
        default=300,
        ge=15,
        description="Seconds between automatic alert-evaluation ticks (floor 15s to avoid hammering NSE).",
    )
    scheduler_refresh_market_data: bool = Field(
        default=True,
        description="Whether each tick fetches fresh NSE snapshots before evaluating. Set False to evaluate only already-persisted snapshots (offline).",
    )
    scheduler_max_symbols_per_tick: int = Field(
        default=0,
        ge=0,
        description="Upper bound on held or lent symbols refreshed from NSE per tick (0 = no cap). A rotating cursor makes the next tick continue where this one stopped; reuses nse_rate_limit_delay_seconds between fetches.",
    )
    alert_cooldown_minutes: int = Field(
        default=45, ge=0, description="Per-rule cooldown after a fire; a re-crossing inside it sends nothing (0 = off)."
    )
    macos_notifications_enabled: bool = Field(
        default=True, description="Show alerts as macOS notifications when running on a Mac (Phase 5.1)."
    )
    opportunity_quote_max_age_seconds: int = Field(default=86400, ge=60, description="Maximum age of a bid eligible for opportunity alerts.")
    scheduler_batch_series_enabled: bool = Field(default=False, description="Refresh held stocks by the twelve Series B month feeds to meet a one-minute cycle.")
    history_cache_dir: str = Field(default="runtime/history", description="Saved SLB daily aggregate history; never individual trade executions.")
    live_refresh_seconds: int = Field(
        default=180,
        ge=30,
        description="Live board: seconds between full sweeps of every polled holding in market hours (editable in Settings).",
    )
    # Phase 3.4: when a lending opportunity is actionable.
    min_net_yield_pct: float = Field(default=4.0, ge=0.0, description="Net annualised yield floor for an actionable opportunity (%).")
    min_fill_qty: int = Field(default=1, ge=1, description="Smallest fillable quantity (min(lendable, bid qty)) worth acting on.")
    min_net_fee_inr: float = Field(default=0.0, ge=0.0, description="Smallest net fee (INR) worth acting on; 0 = off.")
    tracked_expiries: int = Field(default=3, ge=1, le=6, description="How many upcoming Series B expiries the opportunity board shows per stock.")
    rollover_warn_days: int = Field(
        default=5, ge=1, description="ROLLOVER alert: days before the reverse leg to warn (Phase 4.5)."
    )
    series_family: str = Field(
        default="B",
        description="Series family the lending views read (alerts, dashboard, rollover, live board). Zerodha lends Series B only (decided 4 Oct 2026). Env-only.",
    )
    scheduler_market_hours_only: bool = Field(
        default=True,
        description="Only call NSE 09:00-17:15 IST, Monday to Friday (no holiday calendar, by design). Alert evaluation still runs on cached snapshots outside those hours.",
    )
    alert_scheduler_startup_delay_seconds: int = Field(
        default=10,
        ge=0,
        description="Grace delay before the first tick so startup/seeding settles.",
    )
    snapshot_retention_days: int = Field(
        default=90,
        ge=0,
        description=(
            "Each scheduler tick deletes slb_snapshots older than this many days "
            "(0 = keep forever). Bounds the unbounded growth that continuous "
            "auto-ingestion would otherwise cause (~7k rows/day at the default "
            "cadence). Safe for every rule: YIELD_THRESHOLD/TIGHT_SPREAD read only "
            "the latest snapshot per (symbol, expiry) and ABOVE_AVERAGE reads the "
            "last above_average_lookback_samples rows — any sane horizon keeps far "
            "more than those need."
        ),
    )

    # --- Phase 5A email/SMTP alert delivery (env-only secrets) ---
    # These activate the previously-inert SMTP seam. They are read from the
    # environment / .env ONLY and are deliberately secrets: they are never stored
    # in the DB, never returned by any API response (excluded from
    # SettingsResponse), and never included in the 5D editable-settings set. Email
    # delivery stays OFF until smtp_host AND alert_email_to are provided (and
    # notifications_enabled is True); see AlertDeliveryService._email_enabled.
    smtp_host: str = Field(
        default="",
        description="SMTP server host. Empty = email channel disabled (in-app delivery only).",
    )
    smtp_port: int = Field(default=587, description="SMTP server port (587 = STARTTLS submission).")
    smtp_user: str = Field(default="", description="SMTP auth username (optional; empty = no login).")
    smtp_password: str = Field(default="", description="SMTP auth password (secret; env-only).")
    smtp_use_tls: bool = Field(
        default=True, description="Issue STARTTLS after connecting before authenticating/sending."
    )
    smtp_from_addr: str = Field(
        default="",
        description="From address for alert emails. Falls back to smtp_user when empty.",
    )
    # Phase 5.3: WhatsApp via Twilio. ENV-ONLY secrets: never stored in the DB,
    # logged, or returned by the API. The channel is on only when all four are set.
    twilio_account_sid: str = Field(default="", description="Twilio account SID (secret; env-only).")
    twilio_auth_token: str = Field(default="", description="Twilio auth token (secret; env-only).")
    whatsapp_from: str = Field(default="", description="Twilio WhatsApp sender, e.g. +14155238886 (env-only).")
    whatsapp_to: str = Field(default="", description="Your WhatsApp number, e.g. +9198... (env-only).")
    alert_email_to: str = Field(
        default="",
        description="Recipient address for alert emails. Empty = email channel disabled.",
    )

    # Server settings
    api_host: str = Field(default="127.0.0.1", description="FastAPI bind address")
    api_port: int = Field(default=8000, description="FastAPI port")
    log_level: str = Field(default="INFO", description="Log level: DEBUG, INFO, WARNING, ERROR")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached instance of application settings."""
    return Settings()
