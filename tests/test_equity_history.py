"""Phase 5B tests: equity price/volume history (parse, chunked fetch, upsert)
and SLB snapshot trend / descriptive equity stats — all offline.

No network: the fetch path is exercised with an injected fake async client, so
the parse→merge→upsert pipeline is covered without touching NSE.
"""

from __future__ import annotations

import datetime

from sqlalchemy.orm import Session

from slbm_tracker.db.models import SLBSnapshot, Stock, StockPriceHistory
from slbm_tracker.services.analytics_service import AnalyticsService
from slbm_tracker.services.contract_service import today_ist
from slbm_tracker.services.equity_history_service import (
    EquityBar,
    EquityHistoryService,
    _windows,
    parse_equity_history,
    parse_nse_date,
)


class FakeNSEClient:
    """Async stand-in for NSEClient, records calls.

    Initialize with a list to serve payloads positionally (one per call, in
    order — used for single-symbol multi-window fetches), or with a dict keyed
    by symbol to serve per-symbol (order-independent — used when several symbols
    are refreshed and iteration order must not matter to the assertion).
    """

    def __init__(self, payloads):
        self.by_symbol = dict(payloads) if isinstance(payloads, dict) else None
        self.payloads = None if self.by_symbol is not None else list(payloads)
        self.calls = []

    async def fetch_equity_history(self, symbol, from_date, to_date, series="EQ"):
        self.calls.append((symbol, from_date, to_date, series))
        if self.by_symbol is not None:
            return self.by_symbol.get(symbol, {"data": []})
        return self.payloads.pop(0) if self.payloads else {"data": []}


def _row(date_str, close, **extra):
    row = {"CH_TIMESTAMP": date_str, "CH_SYMBOL": "RELIANCE", "CH_CLOSING_PRICE": close}
    row.update(extra)
    return row


# --- parser -----------------------------------------------------------------

def test_parse_maps_fields_and_dates():
    bars = parse_equity_history(
        {"data": [_row("2024-01-15", "2500.5", CH_OPENING_PRICE="2495",
                       CH_TRADE_HIGH_PRICE="2510", CH_TRADE_LOW_PRICE="2490",
                       VWAP="2501", CH_TOT_TRADED_QTY="1000",
                       COP_DELIV_QTY="600", COP_DELIV_PERC="60")]}
    )
    assert len(bars) == 1
    b = bars[0]
    assert b.symbol == "RELIANCE"
    assert b.date == datetime.date(2024, 1, 15)
    assert (b.open, b.high, b.low, b.close, b.vwap) == (2495.0, 2510.0, 2490.0, 2500.5, 2501.0)
    assert (b.volume, b.delivery_qty, b.delivery_pct) == (1000, 600, 60.0)


def test_parse_missing_fields_become_none_not_zero():
    bars = parse_equity_history({"data": [{"CH_TIMESTAMP": "2024-01-15", "CH_SYMBOL": "X"}]})
    b = bars[0]
    assert b.close is None and b.volume is None and b.delivery_pct is None


def test_parse_tolerates_bad_shapes_and_bad_dates():
    assert parse_equity_history(None) == []
    assert parse_equity_history({"nope": 1}) == []
    assert parse_equity_history({"data": "notalist"}) == []
    # A row with an unparseable date is skipped, not fabricated.
    assert parse_equity_history({"data": [{"CH_TIMESTAMP": "??", "CH_CLOSING_PRICE": "1"}]}) == []


def test_parse_nse_date_both_formats():
    assert parse_nse_date("2024-03-05") == datetime.date(2024, 3, 5)
    assert parse_nse_date("05-Mar-2024") == datetime.date(2024, 3, 5)
    assert parse_nse_date("") is None and parse_nse_date(None) is None


# --- windowing --------------------------------------------------------------

def test_windows_split_adjacent_no_overlap():
    start = datetime.date(2024, 1, 1)
    end = datetime.date(2024, 4, 10)  # 100 days inclusive
    wins = _windows(start, end, 90)
    assert len(wins) == 2
    assert wins[0][0] == start
    # Adjacent, non-overlapping: next window starts the day after prior end.
    assert wins[1][0] == wins[0][1] + datetime.timedelta(days=1)
    assert wins[-1][1] == end


# --- upsert -----------------------------------------------------------------

def test_upsert_dedupes_on_symbol_date_and_updates(db: Session):
    svc = EquityHistoryService(db, client=FakeNSEClient([]))
    d1, d2, d3 = datetime.date(2024, 1, 1), datetime.date(2024, 1, 2), datetime.date(2024, 1, 3)
    n1 = svc._upsert_bars("RELIANCE", [
        EquityBar("RELIANCE", d1, close=100.0),
        EquityBar("RELIANCE", d2, close=110.0),
    ])
    assert n1 == 2
    # Re-upsert overlapping d2 (new value) + new d3 → no duplicate row for d2.
    n2 = svc._upsert_bars("RELIANCE", [
        EquityBar("RELIANCE", d2, close=999.0),
        EquityBar("RELIANCE", d3, close=120.0),
    ])
    assert n2 == 2
    rows = db.query(StockPriceHistory).filter_by(symbol="RELIANCE").all()
    assert len(rows) == 3  # d1, d2, d3 — d2 updated in place, not duplicated
    d2_row = db.query(StockPriceHistory).filter_by(symbol="RELIANCE", date=d2).one()
    assert d2_row.close == 999.0


# --- chunked fetch/refresh (offline via fake client) ------------------------

async def test_refresh_symbol_single_window(db: Session):
    fake = FakeNSEClient([{"data": [_row("2024-02-01", "100"), _row("2024-02-02", "101")]}])
    svc = EquityHistoryService(db, client=fake)
    written = await svc.refresh_symbol("reliance", days=30)  # < chunk → one call
    assert written == 2
    assert len(fake.calls) == 1
    assert fake.calls[0][0] == "RELIANCE"  # normalized upper-case
    assert db.query(StockPriceHistory).filter_by(symbol="RELIANCE").count() == 2


async def test_refresh_symbol_multi_window_merges_and_dedupes(db: Session):
    # days=100 with chunk 90 → 2 windows/2 calls; overlapping date B is deduped,
    # later window winning on the close value.
    p1 = {"data": [_row("2024-03-01", "10"), _row("2024-03-02", "20")]}
    p2 = {"data": [_row("2024-03-02", "999"), _row("2024-03-03", "30")]}
    fake = FakeNSEClient([p1, p2])
    svc = EquityHistoryService(db, client=fake)
    written = await svc.refresh_symbol("RELIANCE", days=100)
    assert len(fake.calls) == 2
    assert written == 3  # 3 distinct dates
    b = db.query(StockPriceHistory).filter_by(symbol="RELIANCE", date=datetime.date(2024, 3, 2)).one()
    assert b.close == 999.0


async def test_refresh_owned_only_iterates_owned_symbols(db: Session):
    db.add_all([
        Stock(symbol="RELIANCE", quantity=10, avg_buy_price=2500.0),
        Stock(symbol="INFY", quantity=5, avg_buy_price=1500.0),
        Stock(symbol="ZERO", quantity=0, avg_buy_price=100.0),  # not owned
    ])
    db.commit()
    fake = FakeNSEClient({
        "RELIANCE": {"data": [_row("2024-04-01", "1")]},
        "INFY": {"data": [_row("2024-04-01", "2"), _row("2024-04-02", "3")]},
    })
    svc = EquityHistoryService(db, client=fake)
    result = await svc.refresh_owned(days=30)
    assert set(result) == {"RELIANCE", "INFY"}  # ZERO (qty 0) excluded
    assert result["INFY"] == 2


# --- analytics: SLB snapshot trend -----------------------------------------

def _snap(db, symbol, ts, y, bid, ask, ops):
    db.add(SLBSnapshot(series_family="B",
        symbol=symbol, expiry_date=datetime.date(2026, 11, 3), timestamp=ts,
        annualized_yield=y, best_bid=bid, best_ask=ask, open_positions=ops,
    ))


def test_yield_history_aggregates_per_day(db: Session):
    now = datetime.datetime.utcnow()
    day0 = now.replace(hour=10, minute=0, second=0, microsecond=0)
    day_minus2 = day0 - datetime.timedelta(days=2)
    # Two samples on day0 (mean), one on day-2.
    _snap(db, "RELIANCE", day0, 8.0, 4.0, 5.0, 100)
    _snap(db, "RELIANCE", day0, 9.0, 4.0, 6.0, 200)
    _snap(db, "RELIANCE", day_minus2, 7.0, 3.0, 4.0, 50)
    db.commit()

    pts = AnalyticsService.yield_history(db, "reliance", days=30)
    assert [p.date for p in pts] == sorted(p.date for p in pts)  # oldest-first
    last = pts[-1]  # day0
    assert last.samples == 2
    assert last.annualized_yield == 8.5           # mean(8,9)
    assert last.spread == 1.5                      # mean((5-4),(6-4))
    assert last.open_positions == 150              # mean(100,200)


def test_portfolio_yield_history_restricted_to_owned(db: Session):
    db.add(Stock(symbol="RELIANCE", quantity=10, avg_buy_price=2500.0))
    now = datetime.datetime.utcnow()
    _snap(db, "RELIANCE", now, 8.0, 4.0, 5.0, 100)
    _snap(db, "INFY", now, 12.0, 1.0, 2.0, 10)  # not owned → excluded
    db.commit()
    pts = AnalyticsService.portfolio_yield_history(db, days=30)
    assert len(pts) == 1
    assert pts[0].annualized_yield == 8.0  # only RELIANCE counted, no INFY

    # No owned holdings → empty (never fabricated).
    db.query(Stock).delete()
    db.commit()
    assert AnalyticsService.portfolio_yield_history(db, days=30) == []


# --- analytics: equity descriptive stats -----------------------------------

def _price(db, symbol, d, close, high=None, low=None, volume=None):
    db.add(StockPriceHistory(symbol=symbol, date=d, close=close, high=high, low=low, volume=volume))


def test_equity_stats_descriptive(db: Session):
    base = today_ist()
    _price(db, "RELIANCE", base - datetime.timedelta(days=4), 100.0, high=105, low=95, volume=1000)
    _price(db, "RELIANCE", base - datetime.timedelta(days=3), 110.0, high=112, low=108, volume=2000)
    _price(db, "RELIANCE", base - datetime.timedelta(days=2), 99.0, high=115, low=90, volume=1500)
    # Outside a 30-day window → must be excluded from bars and stats.
    _price(db, "RELIANCE", base - datetime.timedelta(days=400), 5.0, high=5, low=5, volume=1)
    db.commit()

    bars = AnalyticsService.equity_history(db, "RELIANCE", days=30)
    assert len(bars) == 3  # old row excluded by window

    stats = AnalyticsService.equity_stats(db, "reliance", days=30)
    assert stats.samples == 3
    assert stats.first_close == 100.0 and stats.last_close == 99.0
    assert stats.period_high == 115.0 and stats.period_low == 90.0
    assert stats.period_return_pct == -1.0            # (99-100)/100*100
    assert stats.avg_volume == 1500
    assert stats.daily_return_volatility_pct == 14.1421  # stdev([+10%, -10%])


def test_equity_stats_empty_is_all_none(db: Session):
    stats = AnalyticsService.equity_stats(db, "NOPE", days=30)
    assert stats.samples == 0
    assert stats.first_close is None and stats.period_return_pct is None
    assert stats.daily_return_volatility_pct is None
