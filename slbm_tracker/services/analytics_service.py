"""Analytics aggregations (Phase 3 feature 6).

Read-only roll-ups over ACTUAL persisted data — P&L records, derived contract
lifecycle state, and captured market snapshots. No forecasting, no invented
data (Phase 3 scope control): a symbol with no snapshots simply does not appear
in the yield view. All money sums are done in Decimal, never binary float.

Financial-year grouping reuses the single canonical Apr 1–Mar 31 rule from
:class:`DashboardService` so the two views can never drift apart.
"""

from __future__ import annotations

import datetime
import logging
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from slbm_tracker.db.models import (
    LendingContract,
    PnLRecord,
    SLBSnapshot,
    Stock,
    StockPriceHistory,
)
from slbm_tracker.services.contract_service import ContractService, today_ist, IST
from slbm_tracker.services.dashboard_service import DashboardService
from slbm_tracker.utils.money import money_to_float, to_decimal

logger = logging.getLogger(__name__)

_MONEY_FIELDS = (
    "gross_fee",
    "processing_charge",
    "gst",
    "dp_charge",
    "net_income",
    "estimated_tax",
    "post_tax_income",
)


@dataclass(frozen=True)
class FinancialYearIncome:
    """P&L totals for one Indian financial year (Apr 1–Mar 31)."""

    financial_year: str  # e.g. "2026-2027"
    contracts: int
    gross_fee: float
    net_income: float
    estimated_tax: float
    post_tax_income: float


@dataclass(frozen=True)
class SymbolYield:
    """Yield stats for one symbol, from persisted snapshots only."""

    symbol: str
    latest_yield: Optional[float]
    average_yield: Optional[float]
    samples: int


@dataclass(frozen=True)
class AnalyticsSummary:
    """Combined analytics payload."""

    generated_at: datetime.datetime
    status_breakdown: dict  # effective_status -> count
    income_by_financial_year: list[FinancialYearIncome] = field(default_factory=list)
    yield_by_symbol: list[SymbolYield] = field(default_factory=list)


@dataclass(frozen=True)
class SLBHistoryPoint:
    """One calendar day's aggregated SLB metrics for a symbol or the portfolio.

    Snapshots are captured many times per day by the scheduler; this collapses
    them to one point per date (mean of that day's samples) so trends read
    cleanly over a multi-week window. Every figure comes from stored snapshot
    columns — spread is derived honestly as ask-bid, never fabricated.
    """

    date: str  # ISO date (YYYY-MM-DD)
    annualized_yield: Optional[float]
    spread: Optional[float]
    open_positions: Optional[int]
    samples: int


@dataclass(frozen=True)
class EquityStats:
    """Descriptive-only statistics over an equity's persisted price history.

    No forecasting: every field describes the observed window. Volatility is the
    sample standard deviation of daily close-to-close percentage returns — a
    backward-looking dispersion measure, not a prediction. Prices/percentages are
    market values (not INR money totals), so they use ordinary rounding rather
    than the Decimal money-quantization rule reserved for currency sums.
    """

    symbol: str
    samples: int
    period_start: Optional[str]  # ISO date
    period_end: Optional[str]  # ISO date
    first_close: Optional[float]
    last_close: Optional[float]
    period_high: Optional[float]
    period_low: Optional[float]
    period_return_pct: Optional[float]
    avg_volume: Optional[int]
    daily_return_volatility_pct: Optional[float]


class AnalyticsService:
    """Deterministic read-only analytics over persisted data."""

    @classmethod
    def income_by_financial_year(cls, db: Session) -> list[FinancialYearIncome]:
        """Group persisted P&L by the FY of each contract's expiry date."""
        rows = (
            db.query(PnLRecord, LendingContract.expiry_date)
            .join(LendingContract, PnLRecord.contract_id == LendingContract.id)
            .all()
        )
        buckets: dict[str, dict] = defaultdict(
            lambda: {"contracts": 0, **{f: to_decimal(0) for f in _MONEY_FIELDS}}
        )
        for record, expiry in rows:
            fy_start, fy_end = DashboardService._financial_year_bounds(expiry)
            key = f"{fy_start.year}-{fy_end.year}"
            b = buckets[key]
            b["contracts"] += 1
            for f in _MONEY_FIELDS:
                b[f] += to_decimal(getattr(record, f))

        result = [
            FinancialYearIncome(
                financial_year=key,
                contracts=b["contracts"],
                gross_fee=money_to_float(b["gross_fee"]),
                net_income=money_to_float(b["net_income"]),
                estimated_tax=money_to_float(b["estimated_tax"]),
                post_tax_income=money_to_float(b["post_tax_income"]),
            )
            for key, b in buckets.items()
        ]
        result.sort(key=lambda r: r.financial_year)
        return result

    @classmethod
    def status_breakdown(cls, db: Session, today: Optional[datetime.date] = None) -> dict:
        """Count contracts by DERIVED effective status (never stored/drifted)."""
        today = today or today_ist()
        counts: dict[str, int] = defaultdict(int)
        for view in ContractService.list_contracts(db=db, today=today):
            counts[view.effective_status] += 1
        return dict(counts)

    @classmethod
    def yield_by_symbol(cls, db: Session) -> list[SymbolYield]:
        """Latest + average annualized yield per symbol, from snapshots only."""
        rows = (
            db.query(SLBSnapshot.symbol, SLBSnapshot.annualized_yield, SLBSnapshot.timestamp)
            .filter(SLBSnapshot.annualized_yield.isnot(None), SLBSnapshot.series_family == "B")
            .order_by(SLBSnapshot.timestamp.desc())
            .all()
        )
        by_symbol: dict[str, list] = defaultdict(list)
        for symbol, y, ts in rows:
            by_symbol[symbol].append((ts, y))

        out: list[SymbolYield] = []
        for symbol, points in by_symbol.items():
            # points already newest-first (query order preserved per symbol).
            latest = points[0][1]
            ys = [to_decimal(y) for _, y in points]
            avg = float(sum(ys) / to_decimal(len(ys)))
            out.append(
                SymbolYield(
                    symbol=symbol,
                    latest_yield=float(latest),
                    average_yield=round(avg, 2),
                    samples=len(points),
                )
            )
        out.sort(key=lambda s: (s.latest_yield if s.latest_yield is not None else -1.0), reverse=True)
        return out

    @classmethod
    def _aggregate_daily(cls, rows: list) -> list[SLBHistoryPoint]:
        """Collapse (timestamp, yield, bid, ask, open_positions) rows to one
        SLBHistoryPoint per calendar date (mean of that day's samples)."""
        by_date: dict[datetime.date, dict] = defaultdict(
            lambda: {"yields": [], "spreads": [], "ops": [], "samples": 0}
        )
        for ts, y, bid, ask, ops in rows:
            d = ts.replace(tzinfo=datetime.timezone.utc).astimezone(IST).date()
            bucket = by_date[d]
            bucket["samples"] += 1
            if y is not None:
                bucket["yields"].append(to_decimal(y))
            if bid is not None and ask is not None:
                bucket["spreads"].append(to_decimal(ask) - to_decimal(bid))
            if ops is not None:
                bucket["ops"].append(to_decimal(ops))

        points: list[SLBHistoryPoint] = []
        for d in sorted(by_date):
            b = by_date[d]
            avg_yield = (
                round(float(sum(b["yields"]) / to_decimal(len(b["yields"]))), 2)
                if b["yields"]
                else None
            )
            avg_spread = (
                round(float(sum(b["spreads"]) / to_decimal(len(b["spreads"]))), 4)
                if b["spreads"]
                else None
            )
            avg_ops = (
                int(round(float(sum(b["ops"]) / to_decimal(len(b["ops"])))))
                if b["ops"]
                else None
            )
            points.append(
                SLBHistoryPoint(
                    date=d.isoformat(),
                    annualized_yield=avg_yield,
                    spread=avg_spread,
                    open_positions=avg_ops,
                    samples=b["samples"],
                )
            )
        return points

    @classmethod
    def yield_history(cls, db: Session, symbol: str, days: int) -> list[SLBHistoryPoint]:
        """Daily SLB yield/spread/open-positions trend for one symbol over the
        trailing ``days`` window (from persisted snapshots only)."""
        cutoff = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - datetime.timedelta(days=days)
        rows = (
            db.query(
                SLBSnapshot.timestamp,
                SLBSnapshot.annualized_yield,
                SLBSnapshot.best_bid,
                SLBSnapshot.best_ask,
                SLBSnapshot.open_positions,
            )
            .filter(SLBSnapshot.symbol == symbol.strip().upper())
            .filter(SLBSnapshot.timestamp >= cutoff, SLBSnapshot.series_family == "B")
            .order_by(SLBSnapshot.timestamp.asc())
            .all()
        )
        return cls._aggregate_daily(rows)

    @classmethod
    def portfolio_yield_history(cls, db: Session, days: int) -> list[SLBHistoryPoint]:
        """Daily SLB trend aggregated across OWNED symbols (quantity > 0). Empty
        when nothing is owned or no snapshots fall in the window."""
        owned = [s for (s,) in db.query(Stock.symbol).filter(Stock.quantity > 0).all()]
        if not owned:
            return []
        cutoff = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - datetime.timedelta(days=days)
        rows = (
            db.query(
                SLBSnapshot.timestamp,
                SLBSnapshot.annualized_yield,
                SLBSnapshot.best_bid,
                SLBSnapshot.best_ask,
                SLBSnapshot.open_positions,
            )
            .filter(SLBSnapshot.symbol.in_(owned))
            .filter(SLBSnapshot.timestamp >= cutoff, SLBSnapshot.series_family == "B")
            .order_by(SLBSnapshot.timestamp.asc())
            .all()
        )
        return cls._aggregate_daily(rows)

    @classmethod
    def equity_history(
        cls, db: Session, symbol: str, days: int
    ) -> list[StockPriceHistory]:
        """Persisted daily price/volume bars for a symbol within the trailing
        ``days`` window, oldest first. Returns [] when nothing is stored."""
        start = today_ist() - datetime.timedelta(days=days - 1)
        return (
            db.query(StockPriceHistory)
            .filter(StockPriceHistory.symbol == symbol.strip().upper())
            .filter(StockPriceHistory.date >= start)
            .order_by(StockPriceHistory.date.asc())
            .all()
        )

    @classmethod
    def equity_stats(cls, db: Session, symbol: str, days: int) -> EquityStats:
        """Descriptive statistics over the stored price window (no forecasting).

        first/last close, period high/low, period return %, average volume, and
        the sample stddev of daily close-to-close returns. Absent when < 1 close
        (all stats None) or < 2 closes (volatility None only)."""
        sym = symbol.strip().upper()
        rows = cls.equity_history(db, sym, days)
        closes = [(r.date, r.close) for r in rows if r.close is not None]
        if not closes:
            return EquityStats(
                symbol=sym, samples=0, period_start=None, period_end=None,
                first_close=None, last_close=None, period_high=None, period_low=None,
                period_return_pct=None, avg_volume=None, daily_return_volatility_pct=None,
            )

        first_close = closes[0][1]
        last_close = closes[-1][1]
        # High/low fall back to close when the extreme columns are missing.
        highs = [r.high if r.high is not None else r.close for r in rows if (r.high is not None or r.close is not None)]
        lows = [r.low if r.low is not None else r.close for r in rows if (r.low is not None or r.close is not None)]
        period_high = round(max(highs), 2) if highs else None
        period_low = round(min(lows), 2) if lows else None

        period_return_pct = (
            round((last_close - first_close) / first_close * 100, 2)
            if first_close
            else None
        )

        volumes = [r.volume for r in rows if r.volume is not None]
        avg_volume = int(round(sum(volumes) / len(volumes))) if volumes else None

        # Daily close-to-close percentage returns → sample stddev (needs >= 2).
        daily_returns = [
            (closes[i][1] / closes[i - 1][1] - 1.0) * 100.0
            for i in range(1, len(closes))
            if closes[i - 1][1]
        ]
        volatility = (
            round(statistics.stdev(daily_returns), 4) if len(daily_returns) >= 2 else None
        )

        return EquityStats(
            symbol=sym,
            samples=len(rows),
            period_start=closes[0][0].isoformat(),
            period_end=closes[-1][0].isoformat(),
            first_close=round(first_close, 2),
            last_close=round(last_close, 2),
            period_high=period_high,
            period_low=period_low,
            period_return_pct=period_return_pct,
            avg_volume=avg_volume,
            daily_return_volatility_pct=volatility,
        )

    @classmethod
    def build_summary(
        cls, db: Session, today: Optional[datetime.date] = None
    ) -> AnalyticsSummary:
        return AnalyticsSummary(
            generated_at=datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None),
            status_breakdown=cls.status_breakdown(db, today),
            income_by_financial_year=cls.income_by_financial_year(db),
            yield_by_symbol=cls.yield_by_symbol(db),
        )
