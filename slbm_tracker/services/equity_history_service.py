"""Equity price/volume history: NSE fetch, tolerant parse, and local upsert (Phase 5B).

This module persists *descriptive* daily market history for owned symbols so the
History view can chart price/volume and compute descriptive statistics (period
high/low, period return, daily-return volatility) WITHOUT forecasting anything
and without re-hitting NSE on every view.

Two responsibilities, kept separate:
  * ``parse_equity_history`` — a pure, null-tolerant mapper from NSE's
    security-wise historical JSON to ``EquityBar`` records. Fields absent from a
    row are ``None``, never fabricated (same discipline as the SLB parser).
  * ``EquityHistoryService`` — orchestrates the chunked fetch (NSE caps the date
    range per request) and UPSERTs bars keyed on (symbol, date), so a re-fetch
    updates in place rather than duplicating.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy.orm import Session

from slbm_tracker.config import Settings, get_settings
from slbm_tracker.db.models import Stock, StockPriceHistory
from slbm_tracker.services.contract_service import today_ist
from slbm_tracker.services.nse_client import NSEClient

logger = logging.getLogger(__name__)

# NSE month abbreviations used in the "DD-Mon-YYYY" date form some payloads use.
_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


@dataclass(frozen=True)
class EquityBar:
    """One trading day's OHLC/volume for a symbol. Optional fields are None when
    the source row omits them — never coerced to zero."""

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


def _opt_float(val: Any) -> Optional[float]:
    """Parse a float, returning None (not 0.0) for missing/blank values."""
    if val is None or val == "" or val == "-":
        return None
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _opt_int(val: Any) -> Optional[int]:
    """Parse an int, returning None (not 0) for missing/blank values."""
    f = _opt_float(val)
    return int(f) if f is not None else None


def _first(item: dict[str, Any], *keys: str) -> Any:
    """Return the first present, non-empty value among candidate keys.

    NSE's historical payload key names vary across endpoints/versions, so each
    logical field is looked up across the known aliases rather than assuming one.
    """
    for k in keys:
        if k in item:
            v = item[k]
            if v is not None and v != "" and v != "-":
                return v
    return None


def parse_nse_date(raw: Any) -> Optional[datetime.date]:
    """Parse an NSE date cell in either 'YYYY-MM-DD' or 'DD-Mon-YYYY' form."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    # ISO first (NSE historical CH_TIMESTAMP is 'YYYY-MM-DD').
    try:
        return datetime.date.fromisoformat(s[:10])
    except ValueError:
        pass
    # Fallback: '15-Jan-2024'.
    parts = s.split("-")
    if len(parts) == 3 and parts[1][:3].upper() in _MONTHS:
        try:
            return datetime.date(int(parts[2]), _MONTHS[parts[1][:3].upper()], int(parts[0]))
        except ValueError:
            return None
    return None


def parse_equity_history(response_data: Any, default_symbol: str = "") -> list[EquityBar]:
    """Map an NSE security-wise historical JSON payload to EquityBar records.

    Tolerant of the payload shape (``{"data": [...]}`` or a bare list) and of key
    naming across NSE endpoint versions. Rows without a parseable date are
    skipped. Absent numeric fields become None (never fabricated).
    """
    if isinstance(response_data, dict):
        rows = response_data.get("data")
    else:
        rows = response_data
    if not isinstance(rows, list):
        return []

    bars: list[EquityBar] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        d = parse_nse_date(_first(item, "CH_TIMESTAMP", "mTIMESTAMP", "TIMESTAMP", "date"))
        if d is None:
            continue
        symbol = str(_first(item, "CH_SYMBOL", "symbol") or default_symbol).strip().upper()
        if not symbol:
            continue
        bars.append(
            EquityBar(
                symbol=symbol,
                date=d,
                open=_opt_float(_first(item, "CH_OPENING_PRICE", "open")),
                high=_opt_float(_first(item, "CH_TRADE_HIGH_PRICE", "high")),
                low=_opt_float(_first(item, "CH_TRADE_LOW_PRICE", "low")),
                close=_opt_float(_first(item, "CH_CLOSING_PRICE", "close")),
                vwap=_opt_float(_first(item, "VWAP", "CH_VWAP", "vwap")),
                volume=_opt_int(_first(item, "CH_TOT_TRADED_QTY", "totalTradedVolume", "volume")),
                delivery_qty=_opt_int(_first(item, "COP_DELIV_QTY", "deliveryQuantity")),
                delivery_pct=_opt_float(_first(item, "COP_DELIV_PERC", "deliveryToTradedQuantity")),
            )
        )
    return bars


def _windows(
    start: datetime.date, end: datetime.date, chunk_days: int
) -> list[tuple[datetime.date, datetime.date]]:
    """Split [start, end] into adjacent, non-overlapping <=chunk_days windows."""
    out: list[tuple[datetime.date, datetime.date]] = []
    cur = start
    while cur <= end:
        w_end = min(cur + datetime.timedelta(days=chunk_days - 1), end)
        out.append((cur, w_end))
        cur = w_end + datetime.timedelta(days=1)
    return out


class EquityHistoryService:
    """Fetch (chunked) and persist (upsert) daily equity history for symbols.

    The NSE client is created lazily and owned by the service unless one is
    injected (tests inject a fake async client), so refresh works fully offline
    under test. All fetched bars are UPSERTed on (symbol, date).
    """

    def __init__(
        self,
        db: Session,
        client: Optional[Any] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self.db = db
        self.settings = settings or get_settings()
        self._client = client
        self._owns_client = client is None

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = NSEClient(self.settings)
        return self._client

    async def aclose(self) -> None:
        """Close the NSE client only if this service created it."""
        if self._owns_client and self._client is not None:
            await self._client.close()

    async def refresh_symbol(self, symbol: str, days: Optional[int] = None) -> int:
        """Fetch history for one symbol over the lookback window and upsert it.

        Long windows are fetched in adjacent chunks (NSE caps the per-request
        range) and merged/deduped on date before a single upsert pass. Returns
        the number of bars written. NSE errors propagate to the caller so the
        route can surface an honest failure rather than a silent empty result.
        """
        clean = symbol.strip().upper()
        window = days or self.settings.equity_history_days
        end = today_ist()
        start = end - datetime.timedelta(days=window - 1)
        client = self._get_client()

        merged: dict[datetime.date, EquityBar] = {}
        for w_start, w_end in _windows(start, end, self.settings.nse_history_chunk_days):
            payload = await client.fetch_equity_history(clean, w_start, w_end)
            for bar in parse_equity_history(payload, default_symbol=clean):
                # Later windows win on overlap; keeps exactly one bar per date.
                merged[bar.date] = bar

        bars = [merged[d] for d in sorted(merged)]
        return self._upsert_bars(clean, bars)

    async def refresh_owned(self, days: Optional[int] = None) -> dict[str, int]:
        """Refresh history for every owned symbol (quantity > 0). Returns
        {symbol: bars_written}."""
        owned = (
            self.db.query(Stock)
            .filter(Stock.quantity > 0)
            .order_by(Stock.symbol)
            .all()
        )
        result: dict[str, int] = {}
        for stock in owned:
            result[stock.symbol] = await self.refresh_symbol(stock.symbol, days)
        return result

    def _upsert_bars(self, symbol: str, bars: list[EquityBar]) -> int:
        """Insert or update bars keyed on (symbol, date). Returns rows touched."""
        if not bars:
            return 0
        touched = 0
        for bar in bars:
            existing = (
                self.db.query(StockPriceHistory)
                .filter(StockPriceHistory.symbol == symbol, StockPriceHistory.date == bar.date)
                .first()
            )
            if existing is not None:
                existing.open = bar.open
                existing.high = bar.high
                existing.low = bar.low
                existing.close = bar.close
                existing.vwap = bar.vwap
                existing.volume = bar.volume
                existing.delivery_qty = bar.delivery_qty
                existing.delivery_pct = bar.delivery_pct
                existing.source = "NSE_API"
            else:
                self.db.add(
                    StockPriceHistory(
                        symbol=symbol,
                        date=bar.date,
                        open=bar.open,
                        high=bar.high,
                        low=bar.low,
                        close=bar.close,
                        vwap=bar.vwap,
                        volume=bar.volume,
                        delivery_qty=bar.delivery_qty,
                        delivery_pct=bar.delivery_pct,
                        source="NSE_API",
                    )
                )
            touched += 1
        self.db.commit()
        return touched
