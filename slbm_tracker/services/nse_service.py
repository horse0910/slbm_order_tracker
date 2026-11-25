"""NSE Service layer coordinating live fetching, parsing, and persistence of SLB market data."""

from __future__ import annotations

import dataclasses
import datetime
import json
import logging
from typing import Any, Optional
from sqlalchemy.orm import Session

from slbm_tracker.db.models import SLBSnapshot, SLBTradeEvent
from slbm_tracker.services.nse_client import NSEBlockedError, NSEClient, NSEError
from slbm_tracker.services.nse_parser import NSEDataParser, SLBMarketData, series_family
from slbm_tracker.services.nse_reports import NSEReportsService

logger = logging.getLogger(__name__)

# Series code -> month label ('XO' -> 'Oct-2026') from NSE's series master,
# cached for 6 hours (Phase 2.6). Module-level so every request shares it.
SERIES_CACHE_TTL = datetime.timedelta(hours=6)
_series_cache: Optional[tuple[datetime.datetime, dict[str, str]]] = None


def reset_series_cache() -> None:
    global _series_cache
    _series_cache = None


def _first(value: Any) -> Any:
    """NSE wraps scalars in one-element lists ('timestamp': ['01-Oct-2026 17:00:00'])."""
    return (value[0] if isinstance(value, list) and value else value) or None


def _row_from_market_data(md: SLBMarketData) -> dict[str, Any]:
    """A symbol-view or cached record in the quote-row shape (code/family may be None)."""
    return {
        "symbol": md.symbol,
        "series_code": md.series_code,
        "series_family": md.series_family,
        "month": md.series,
        "settlement_date": md.settlement_date or md.expiry_date,
        "settlement_estimated": md.settlement_estimated,
        "best_bid": md.best_bid,
        "best_bid_qty": md.best_bid_qty,
        "best_ask": md.best_ask,
        "best_ask_qty": md.best_ask_qty,
        "ltp": md.ltp,
        "traded_quantity": md.volume,
        "open_positions": md.open_positions,
        "underlying_price": md.underlying_price,
        "annualized_yield": md.annualized_yield,
        "corporate_action": {"purpose": md.corporate_action} if md.corporate_action else None,
        "is_active": bool(md.best_bid or md.best_ask or md.open_positions or md.ltp),
        "as_of": md.timestamp,
    }


class NSEService:
    """Coordinates SLB market data retrieval with database caching and fallback handling."""

    def __init__(self, db: Session, client: Optional[NSEClient] = None) -> None:
        self.db = db
        self.client = client or NSEClient()

    def _persist_snapshots(
        self, market_data_items: list[SLBMarketData], source: str = "NSE_API"
    ) -> list[SLBSnapshot]:
        """Save parsed market data items into the slb_snapshots database table."""
        saved_snapshots: list[SLBSnapshot] = []
        now = datetime.datetime.utcnow()

        for item in market_data_items:
            if not item.expiry_date:
                # Default to end of current month if unknown
                exp = datetime.date(now.year, now.month, 28)
            else:
                exp = item.expiry_date

            snapshot = SLBSnapshot(
                symbol=item.symbol,
                series=item.series,
                expiry_date=exp,
                best_bid=item.best_bid,
                best_ask=item.best_ask,
                ltp=item.ltp,
                volume=item.volume,
                open_positions=item.open_positions,
                annualized_yield=item.annualized_yield,
                underlying_price=item.underlying_price,
                timestamp=item.timestamp or now,
                source=source,
                series_code=item.series_code,
                series_family=item.series_family,
                settlement_date=item.settlement_date,
                settlement_estimated=item.settlement_estimated,
                best_bid_qty=item.best_bid_qty,
                best_ask_qty=item.best_ask_qty,
                raw_data=json.dumps({
                    "best_bid_qty": item.best_bid_qty,
                    "best_ask_qty": item.best_ask_qty,
                    "spread": item.spread,
                    "spread_pct": item.spread_pct,
                    "turnover": item.turnover,
                    "corporate_action": item.corporate_action,
                    "corporate_action_ex_date": (
                        item.corporate_action_ex_date.isoformat() if item.corporate_action_ex_date else None
                    ),
                }),
            )
            self.db.add(snapshot)
            saved_snapshots.append(snapshot)

        if saved_snapshots:
            self.db.commit()
            for s in saved_snapshots:
                self.db.refresh(s)

        return saved_snapshots

    async def get_slb_data_for_symbol(
        self, symbol: str, save_snapshots: bool = True
    ) -> list[SLBMarketData]:
        """
        Fetch SLB market data for a symbol across expiries.
        Falls back to the most recent cached database snapshots if the live request fails.
        """
        clean_sym = symbol.strip().upper()
        try:
            raw_response = await self.client.fetch_slb_by_symbol(clean_sym)
            parsed_items = NSEDataParser.parse_api_response(raw_response, default_symbol=clean_sym)
            if parsed_items and save_snapshots:
                self._persist_snapshots(parsed_items, source="NSE_API")
            return parsed_items
        except NSEError as err:
            logger.warning(
                "Live NSE fetch failed for %s (%s). Falling back to cached database snapshots.",
                clean_sym,
                err,
            )
            return self.get_cached_snapshots_for_symbol(clean_sym)

    async def get_slb_data_for_series(
        self, series: str, save_snapshots: bool = True
    ) -> list[SLBMarketData]:
        """
        Fetch SLB market data for all symbols within a given series month.
        """
        try:
            raw_response = await self.client.fetch_slb_by_series(series)
            parsed_items = NSEDataParser.parse_api_response(raw_response)
            if parsed_items and save_snapshots:
                self._persist_snapshots(parsed_items, source="NSE_API")
            return parsed_items
        except NSEError as err:
            logger.warning("Live NSE fetch failed for series %s: %s", series, err)
            return []

    async def get_slb_quote(
        self,
        symbol: str,
        *,
        save_snapshots: bool = True,
        fallback: bool = True,
        today: Optional[datetime.date] = None,
        now: Optional[datetime.datetime] = None,
    ) -> dict[str, Any]:
        """Full SLB quote for one stock: every series with its code and family.

        Persists each live series (quoted, traded or holding open positions) as
        a snapshot with source ``NSE_QUOTE``. An empty ``data`` list means the
        stock is not on the SLB board, which is an answer, not an error.

        With ``fallback`` (the default): a block (403) serves the last cached
        rows without calling NSE again; any other NSE failure falls back to A's
        symbol view (``source`` says which). With ``fallback=False`` the
        NSEError propagates, so the scheduler can stop its sweep.
        """
        clean_sym = symbol.strip().upper()
        try:
            raw = await self.client.fetch_slb_quote(clean_sym)
        except NSEBlockedError as err:
            if not fallback:
                raise
            logger.warning("NSE blocked the quote for %s (%s); serving cached rows.", clean_sym, err)
            return self._quote_from_cache(clean_sym, blocked=True)
        except NSEError as err:
            if not fallback:
                raise
            logger.warning("NSE quote failed for %s (%s); falling back to the symbol view.", clean_sym, err)
            try:
                raw_view = await self.client.fetch_slb_by_symbol(clean_sym)
            except NSEBlockedError as view_err:
                logger.warning("NSE blocked the symbol view for %s (%s); serving cached rows.", clean_sym, view_err)
                return self._quote_from_cache(clean_sym, blocked=True)
            except NSEError as view_err:
                logger.warning("NSE symbol view failed for %s (%s); serving cached rows.", clean_sym, view_err)
                return self._quote_from_cache(clean_sym, blocked=False)
            items = NSEDataParser.parse_api_response(raw_view, default_symbol=clean_sym)
            if items and save_snapshots:
                self._persist_snapshots(items, source="NSE_API")
            return {
                "symbol": clean_sym,
                "source": "symbol_view",
                "blocked": False,
                "as_of": None,
                "series": [_row_from_market_data(md) for md in items],
            }

        rows = raw.get("data") if isinstance(raw, dict) else None
        parsed = [q for q in (NSEDataParser.parse_quote_row(r, today=today) for r in rows or []) if q]
        # Live series first, then by settlement date and code.
        parsed.sort(
            key=lambda q: (not q["is_active"], q["settlement_date"] or datetime.date.max, q["series_code"])
        )
        trade_events: list[dict[str, Any]] = []
        if save_snapshots:
            now = now or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
            trade_events = self._record_trade_events(clean_sym, parsed, now)
            self._persist_snapshots(
                [
                    dataclasses.replace(NSEDataParser.quote_row_to_market_data(q, clean_sym), timestamp=now)
                    for q in parsed
                    if q["is_active"] and q["settlement_date"]
                ],
                source="NSE_QUOTE",
            )
        first = rows[0] if rows else {}
        return {
            "symbol": clean_sym,
            "source": "quote",
            "blocked": False,
            "company_name": first.get("companyName"),
            "isin": first.get("isisnCode"),
            "as_of": _first(raw.get("timestamp")) if isinstance(raw, dict) else None,
            "open_positions_as_of": _first(raw.get("outPosDate")) if isinstance(raw, dict) else None,
            "series": parsed,
            "trade_events": trade_events,
        }

    def _record_trade_events(
        self, symbol: str, parsed: list[dict[str, Any]], now: datetime.datetime
    ) -> list[dict[str, Any]]:
        """Write an SLBTradeEvent for each series whose traded quantity rose.

        Compared with the previous quote snapshot of the same series code on the
        same IST trading day. A first sweep of the day compares with nothing; a
        fall is a reset (new day), never a negative trade. Inferred, not a tape.
        """
        from slbm_tracker.services.contract_service import IST

        def ist_date(ts: datetime.datetime) -> datetime.date:
            return ts.replace(tzinfo=datetime.timezone.utc).astimezone(IST).date()

        trading_date = ist_date(now)
        events: list[dict[str, Any]] = []
        for q in parsed:
            code = q["series_code"]
            if not code:
                continue
            prev = (
                self.db.query(SLBSnapshot)
                .filter(SLBSnapshot.symbol == symbol, SLBSnapshot.series_code == code)
                .order_by(SLBSnapshot.timestamp.desc(), SLBSnapshot.id.desc())
                .first()
            )
            if prev is None or prev.volume is None or ist_date(prev.timestamp) != trading_date:
                continue
            rise = q["traded_quantity"] - prev.volume
            if rise <= 0:
                continue
            event = SLBTradeEvent(
                symbol=symbol,
                series_code=code,
                series_family=q["series_family"],
                series=q["month"],
                settlement_date=q["settlement_date"],
                quantity=rise,
                last_fee=q["ltp"],
                traded_quantity_total=q["traded_quantity"],
                occurred_at=now,
                trading_date=trading_date,
            )
            self.db.add(event)
            events.append(
                {
                    "symbol": symbol,
                    "series_code": code,
                    "series_family": q["series_family"],
                    "series": q["month"],
                    "settlement_date": q["settlement_date"],
                    "quantity": rise,
                    "last_fee": q["ltp"],
                    "traded_quantity_total": q["traded_quantity"],
                    "occurred_at": now,
                }
            )
        if events:
            self.db.commit()
        return events

    def _quote_from_cache(self, symbol: str, *, blocked: bool) -> dict[str, Any]:
        """Latest stored snapshot per (series code or label, expiry) for one symbol."""
        records = (
            self.db.query(SLBSnapshot)
            .filter(SLBSnapshot.symbol == symbol)
            .order_by(SLBSnapshot.timestamp.desc(), SLBSnapshot.id.desc())
            .limit(500)
            .all()
        )
        seen: set[tuple] = set()
        rows: list[dict[str, Any]] = []
        for rec in records:
            key = (rec.series_code or rec.series, rec.expiry_date)
            if key in seen:
                continue
            seen.add(key)
            rows.append(_row_from_market_data(self._snapshot_to_market_data(rec)))
        # Same order as a live quote: by settlement, then code.
        rows.sort(key=lambda q: (q["settlement_date"] or datetime.date.max, q["series_code"] or q["month"] or ""))
        return {"symbol": symbol, "source": "cache", "blocked": blocked, "as_of": None, "series": rows}

    async def resolve_series(
        self, series: str, now: Optional[datetime.datetime] = None
    ) -> tuple[str, str]:
        """A series code ('XO') or month label ('Oct-2026') -> (code, month).

        A month maps to its Series B code, the family Zerodha lends. Unknown
        input comes back unchanged. The series master is cached for 6 hours; a
        failed or empty fetch is not cached.
        """
        codes = await self._series_codes(now or datetime.datetime.now(datetime.timezone.utc))
        if series in codes:
            return series, codes[series]
        by_month = [code for code, month in codes.items() if month.lower() == series.lower()]
        if by_month:
            preferred = next((c for c in by_month if series_family(c) == "B"), by_month[0])
            return preferred, codes[preferred]
        return series, series

    async def _series_codes(self, now: datetime.datetime) -> dict[str, str]:
        global _series_cache
        if _series_cache and now - _series_cache[0] < SERIES_CACHE_TTL:
            return _series_cache[1]
        master = await self.get_series_master()
        groups = master.get("data") if isinstance(master, dict) else None
        codes = {
            str(item.get("key")): str(item.get("value"))
            for group in (groups.values() if isinstance(groups, dict) else [])
            for item in (group or [])
            if isinstance(item, dict) and item.get("key")
        }
        if codes:
            _series_cache = (now, codes)
        return codes

    async def get_slb_history(
        self, symbol: str, from_date: datetime.date, to_date: datetime.date
    ) -> list[dict[str, Any]]:
        """Daily SLB trades for one stock, oldest first. Raises NSEError if NSE fails."""
        rows = await self.client.fetch_slb_history(symbol.strip().upper(), from_date, to_date)
        parsed = [h for h in (NSEDataParser.parse_history_row(r) for r in rows) if h]
        parsed.sort(
            key=lambda h: (h["trade_date"], h["settlement_date"] or datetime.date.max, h["series_code"])
        )
        return parsed

    async def get_series_master(self) -> dict:
        """Fetch active series dropdown choices."""
        try:
            return await self.client.fetch_series_master()
        except NSEError as err:
            logger.warning("Failed to fetch series master: %s", err)
            return {"data": {}}

    async def get_eligible_securities(self) -> list[str]:
        """Fetch list of symbols eligible for SLB."""
        try:
            return await self.client.fetch_eligible_securities()
        except NSEError as err:
            logger.warning("Failed to fetch eligible securities: %s", err)
            return []

    @staticmethod
    def _snapshot_to_market_data(rec: SLBSnapshot) -> SLBMarketData:
        """Rebuild a market-data record from a stored snapshot.

        Order quantities live in their own columns since Phase 2.5; older rows
        kept them (and spread, turnover, corporate action) in raw_data, so those
        are restored from there. Values genuinely absent (e.g. CSV-sourced
        snapshots) legitimately remain 0.
        """
        extra: dict = {}
        if rec.raw_data:
            try:
                extra = json.loads(rec.raw_data)
            except (ValueError, TypeError):
                extra = {}

        def _num(value, default):
            try:
                return type(default)(value)
            except (ValueError, TypeError):
                return default

        return SLBMarketData(
            symbol=rec.symbol,
            series=rec.series or "",
            expiry_date=rec.expiry_date,
            best_bid=rec.best_bid or 0.0,
            best_bid_qty=rec.best_bid_qty if rec.best_bid_qty is not None else _num(extra.get("best_bid_qty"), 0),
            best_ask=rec.best_ask or 0.0,
            best_ask_qty=rec.best_ask_qty if rec.best_ask_qty is not None else _num(extra.get("best_ask_qty"), 0),
            ltp=rec.ltp or 0.0,
            underlying_price=rec.underlying_price or 0.0,
            spread=_num(extra.get("spread"), 0.0),
            spread_pct=_num(extra.get("spread_pct"), 0.0),
            open_positions=rec.open_positions or 0,
            volume=rec.volume or 0,
            turnover=_num(extra.get("turnover"), 0.0),
            annualized_yield=rec.annualized_yield or 0.0,
            corporate_action=extra.get("corporate_action"),
            series_code=rec.series_code,
            series_family=rec.series_family,
            settlement_date=rec.settlement_date,
            settlement_estimated=rec.settlement_estimated,
            timestamp=rec.timestamp,
        )

    def get_cached_snapshots_for_symbol(self, symbol: str) -> list[SLBMarketData]:
        """Retrieve the most recent cached snapshots for a symbol from the database."""
        clean_sym = symbol.strip().upper()
        # Newest first, keeping only the latest snapshot per expiry (dedup below).
        # The secondary ``id`` sort makes same-timestamp ties deterministic (the
        # most recently inserted row wins) instead of relying on undefined ORDER
        # BY tie ordering. The limit is headroom well beyond the handful of live
        # SLB expiries × recent ticks, so this LIMIT-before-dedup cannot truncate
        # a distinct expiry out of the fallback in practice, while still bounding
        # the scan on this cache-only path.
        records = (
            self.db.query(SLBSnapshot)
            .filter(SLBSnapshot.symbol == clean_sym)
            .order_by(SLBSnapshot.timestamp.desc(), SLBSnapshot.id.desc())
            .limit(200)
            .all()
        )

        results: list[SLBMarketData] = []
        # Keyed by (expiry, series code) so a Series A and a Series B row of the
        # same month are not merged; legacy rows have no code and dedupe by expiry.
        seen: set[tuple] = set()
        for rec in records:
            key = (rec.expiry_date, rec.series_code)
            if key in seen:
                continue
            seen.add(key)
            results.append(self._snapshot_to_market_data(rec))
        return results

    def ingest_csv_report(
        self, csv_content: str, report_date: Optional[datetime.date] = None
    ) -> list[SLBSnapshot]:
        """Parse offline CSV report and persist snapshots."""
        parsed = NSEReportsService.parse_slb_csv_report(csv_content, report_date=report_date)
        return self._persist_snapshots(parsed, source="NSE_CSV")
