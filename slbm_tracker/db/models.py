"""SQLAlchemy data models for SLBM Order Tracker."""

from __future__ import annotations

import datetime
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import relationship

from slbm_tracker.db.session import Base


class Stock(Base):
    """Securities held in portfolio or tracked for SLB opportunities."""

    __tablename__ = "stocks"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(String(200), nullable=True)
    isin = Column(String(20), nullable=True, index=True)
    quantity = Column(Integer, nullable=False, default=0)
    avg_buy_price = Column(Float, nullable=False, default=0.0)
    is_slb_eligible = Column(Boolean, nullable=False, default=True)
    notes = Column(Text, nullable=True)
    # When the last Zerodha import set ``quantity`` (naive UTC, like created_at).
    # Added in Phase 1.5 through db/migrate.py.
    holdings_imported_at = Column(DateTime, nullable=True)
    # Phase 2.12: when a sweep last set is_slb_eligible from NSE (naive UTC).
    slb_checked_at = Column(DateTime, nullable=True)
    # Phase 3.2: shares pledged as collateral at the last import (cannot be lent).
    pledged_quantity = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow, server_default=func.now())
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
        server_default=func.now(),
    )

    # Relationships
    lending_contracts = relationship(
        "LendingContract",
        back_populates="stock",
        cascade="all, delete-orphan",
        order_by="desc(LendingContract.created_at)",
    )
    alerts = relationship(
        "Alert",
        back_populates="stock",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Stock symbol={self.symbol} qty={self.quantity}>"


class LendingContract(Base):
    """An active or completed lending contract where shares have been lent out."""

    __tablename__ = "lending_contracts"

    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False, index=True)
    series = Column(String(10), nullable=True)  # e.g., '01', '02', 'X1'
    expiry_date = Column(Date, nullable=False, index=True)
    quantity_lent = Column(Integer, nullable=False)
    lending_fee = Column(Float, nullable=False)  # Fee per share
    status = Column(
        String(20),
        nullable=False,
        default="ACTIVE",
        index=True,
    )  # ACTIVE, EXPIRED, RECALLED, CLOSED, ROLLED_OVER
    trade_date = Column(Date, nullable=False, default=datetime.date.today)
    reverse_leg_date = Column(Date, nullable=True)
    notes = Column(Text, nullable=True)
    # Phase 4.1 (via db/migrate.py). origin_date: the ORIGINAL contract date a
    # roll chain started on (SEBI's 365-day cap counts from it); rolled_from_id:
    # the contract this one replaced; series_code: e.g. 'XN'; closed_on: the IST
    # date the contract stopped being lent (close, recall, roll or auto-close).
    origin_date = Column(Date, nullable=True)
    rolled_from_id = Column(Integer, nullable=True, index=True)
    series_code = Column(String(10), nullable=True)
    closed_on = Column(Date, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow, server_default=func.now())
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
        server_default=func.now(),
    )

    # Relationships
    stock = relationship("Stock", back_populates="lending_contracts")
    pnl_record = relationship(
        "PnLRecord",
        back_populates="contract",
        uselist=False,
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<LendingContract id={self.id} stock_id={self.stock_id} qty={self.quantity_lent} fee={self.lending_fee} status={self.status}>"


class SLBSnapshot(Base):
    """Historical or live snapshot of SLB market data for a given security and expiry."""

    __tablename__ = "slb_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(50), nullable=False, index=True)
    series = Column(String(10), nullable=True)
    expiry_date = Column(Date, nullable=False, index=True)
    best_bid = Column(Float, nullable=True)
    best_ask = Column(Float, nullable=True)
    ltp = Column(Float, nullable=True)  # Last Traded Lending Fee
    volume = Column(Integer, nullable=True)
    open_positions = Column(Integer, nullable=True)
    annualized_yield = Column(Float, nullable=True)
    underlying_price = Column(Float, nullable=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.datetime.utcnow, index=True)
    source = Column(String(30), nullable=False, default="NSE_API")  # NSE_API, NSE_QUOTE or NSE_CSV
    raw_data = Column(Text, nullable=True)
    # Phase 2.5 (via db/migrate.py; NULL on legacy rows, which never reach
    # lending views). From the quote API: series code ('XN'), family ('A', 'B'
    # or 'SPECIAL'), NSE's reverse-leg date and whether it was estimated.
    series_code = Column(String(10), nullable=True)
    series_family = Column(String(10), nullable=True)
    settlement_date = Column(Date, nullable=True)
    settlement_estimated = Column(Boolean, nullable=True)
    best_bid_qty = Column(Integer, nullable=True)
    best_ask_qty = Column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_slb_snapshots_symbol_expiry_timestamp", "symbol", "expiry_date", "timestamp"),
        Index(
            "ix_slb_snapshots_symbol_family_expiry_ts",
            "symbol", "series_family", "expiry_date", "timestamp",
        ),
    )

    def __repr__(self) -> str:
        return f"<SLBSnapshot symbol={self.symbol} expiry={self.expiry_date} ltp={self.ltp} yield={self.annualized_yield}%>"


class Alert(Base):
    """Configured alert rules and triggers."""

    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=True, index=True)
    alert_type = Column(
        String(50),
        nullable=False,
        index=True,
    )  # YIELD_THRESHOLD, ABOVE_AVERAGE, TIGHT_SPREAD, ROLLOVER, EXPIRY_REMINDER
    threshold = Column(Float, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    is_triggered = Column(Boolean, nullable=False, default=False)
    last_triggered_at = Column(DateTime, nullable=True)
    trigger_message = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow, server_default=func.now())
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
        server_default=func.now(),
    )

    # Relationships
    stock = relationship("Stock", back_populates="alerts")

    def __repr__(self) -> str:
        return f"<Alert id={self.id} type={self.alert_type} threshold={self.threshold} active={self.is_active}>"


class SLBTradeEvent(Base):
    """A rise in one series' traded quantity between two sweeps (Phase 2.13).

    NSE publishes no SLB trade tape: this is inferred, not an individual trade.
    Several trades between sweeps appear as one event with their combined
    quantity and the latest fee. A new table, so ``create_all`` adds it.
    """

    __tablename__ = "slb_trade_events"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(50), nullable=False, index=True)
    series_code = Column(String(10), nullable=False)
    series_family = Column(String(10), nullable=True)
    series = Column(String(20), nullable=True)  # month label, e.g. 'Nov-2026'
    settlement_date = Column(Date, nullable=True)
    quantity = Column(Integer, nullable=False)  # traded since the previous sweep
    last_fee = Column(Float, nullable=True)  # NSE's last traded fee at this sweep
    traded_quantity_total = Column(Integer, nullable=False)  # day's cumulative
    occurred_at = Column(DateTime, nullable=False, index=True)  # sweep time, naive UTC
    trading_date = Column(Date, nullable=False, index=True)  # IST date

    def __repr__(self) -> str:
        return f"<SLBTradeEvent {self.symbol} {self.series_code} +{self.quantity} @ {self.last_fee}>"


class TrackedSymbol(Base):
    """A symbol in the tracked SLB universe.

    This is the configured/seeded list of symbols the application monitors for
    SLB opportunities and offers in symbol selectors. It is deliberately
    SEPARATE from :class:`Stock` (actual portfolio holdings with quantity /
    average price): a tracked symbol is not an owned holding and carries no
    market data. Phase 3 contract/rollover workflows can draw their symbol
    choices from here without contaminating portfolio counts or P&L.
    """

    __tablename__ = "tracked_symbols"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(50), unique=True, index=True, nullable=False)
    created_at = Column(
        DateTime, nullable=False, default=datetime.datetime.utcnow, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<TrackedSymbol symbol={self.symbol}>"


class PnLRecord(Base):
    """P&L and fee breakdown for a lending contract."""

    __tablename__ = "pnl_records"

    id = Column(Integer, primary_key=True, index=True)
    contract_id = Column(
        Integer,
        ForeignKey("lending_contracts.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    gross_fee = Column(Float, nullable=False)
    processing_charge = Column(Float, nullable=False)
    gst = Column(Float, nullable=False)
    dp_charge = Column(Float, nullable=False)
    net_income = Column(Float, nullable=False)
    tax_slab_pct = Column(Float, nullable=False, default=30.0)
    estimated_tax = Column(Float, nullable=False, default=0.0)
    post_tax_income = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow, server_default=func.now())

    # Relationships
    contract = relationship("LendingContract", back_populates="pnl_record")

    def __repr__(self) -> str:
        return f"<PnLRecord contract_id={self.contract_id} gross={self.gross_fee} net={self.net_income}>"


class NotificationDelivery(Base):
    """Audit log of alert-notification delivery attempts (Phase 5A).

    One row per (alert, channel) delivery attempt, written best-effort AFTER the
    trigger has already been committed, so an audit-write failure can never
    destroy a trigger. ``alert_id`` uses ``ondelete=SET NULL`` (and is nullable)
    so the delivery history SURVIVES deletion of the underlying alert rule — the
    record of "an email went out" must outlive the rule that produced it.

    This is a NEW table, so ``Base.metadata.create_all`` picks it up with no
    migration. It stores only non-secret outcome data (channel, delivered flag,
    a human-readable detail string) — never SMTP credentials.
    """

    __tablename__ = "notification_deliveries"

    id = Column(Integer, primary_key=True, index=True)
    alert_id = Column(
        Integer,
        ForeignKey("alerts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    channel = Column(String(20), nullable=False)  # in_app | email | macos | whatsapp
    delivered = Column(Boolean, nullable=False, default=False)
    # Phase 5: sent | skipped | failed (NULL on rows written before Phase 5).
    outcome = Column(String(10), nullable=True)
    detail = Column(Text, nullable=True)
    created_at = Column(
        DateTime, nullable=False, default=datetime.datetime.utcnow, index=True
    )

    def __repr__(self) -> str:
        return (
            f"<NotificationDelivery id={self.id} alert_id={self.alert_id} "
            f"channel={self.channel} delivered={self.delivered}>"
        )


class StockPriceHistory(Base):
    """Persisted daily equity price/volume bar for a symbol (Phase 5B).

    One row per (symbol, trading date), fetched from NSE's security-wise
    historical endpoint and stored locally so the History view can chart
    price/volume and compute DESCRIPTIVE statistics (period high/low, period
    return, daily-return volatility) without re-hitting NSE on every view. This
    is strictly descriptive market history — there is no forecasting anywhere.

    Fields absent from an NSE row are stored as NULL, never fabricated (the same
    discipline the SLB parser follows). This is a NEW table, so
    ``Base.metadata.create_all`` picks it up with no migration and no ALTER of
    any existing table. The UNIQUE index on (symbol, date) is what makes a
    re-fetch UPSERT the existing bar instead of duplicating it.
    """

    __tablename__ = "stock_price_history"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(50), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    open = Column(Float, nullable=True)
    high = Column(Float, nullable=True)
    low = Column(Float, nullable=True)
    close = Column(Float, nullable=True)
    vwap = Column(Float, nullable=True)
    volume = Column(Integer, nullable=True)
    delivery_qty = Column(Integer, nullable=True)
    delivery_pct = Column(Float, nullable=True)
    source = Column(String(30), nullable=False, default="NSE_API")
    created_at = Column(
        DateTime, nullable=False, default=datetime.datetime.utcnow, index=True
    )

    __table_args__ = (
        Index("ix_stock_price_history_symbol_date", "symbol", "date", unique=True),
    )

    def __repr__(self) -> str:
        return (
            f"<StockPriceHistory symbol={self.symbol} date={self.date} "
            f"close={self.close} volume={self.volume}>"
        )


class AppSetting(Base):
    """A persisted runtime override for ONE non-secret business preference (Phase 5D).

    A minimal key/value table. Each row overrides the environment/.env default
    for the corresponding key; a key with no row falls back to the env value, so
    the table is purely additive — it never replaces the config layer, only
    layers on top of it (see :mod:`slbm_tracker.services.business_config`).

    ``value`` is JSON-encoded so a bool / int / float round-trips back to its
    exact Python type rather than becoming a string. Only keys in the resolver's
    whitelist are ever written or honored: SMTP credentials and every other
    secret are NOT representable here, and even a hand-inserted row for a
    non-whitelisted key is ignored on read — a secret can never be persisted via
    settings or served by the API.

    This is a NEW table, so ``Base.metadata.create_all`` picks it up with no
    migration and no ALTER of any existing table.
    """

    __tablename__ = "app_settings"

    key = Column(String(64), primary_key=True)
    value = Column(Text, nullable=False)  # JSON-encoded scalar (bool/int/float)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
    )

    def __repr__(self) -> str:
        return f"<AppSetting key={self.key} value={self.value}>"
