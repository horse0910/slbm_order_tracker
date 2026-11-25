"""Live board read model (Phase 2.13): every holding with its live SLB series.

Reads persisted quote snapshots and inferred trade events only; it never calls
NSE. Sweeps are the scheduler's job. "Trades" are rises in a series' traded
quantity between sweeps (NSE has no SLB trade tape), and are labelled so.
"""

from __future__ import annotations

import datetime
from typing import Any, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from slbm_tracker.config import get_settings
from slbm_tracker.db.models import LendingContract, SLBSnapshot, SLBTradeEvent, Stock
from slbm_tracker.services import business_config, lending_economics, equity_quote_service
from slbm_tracker.services.contract_service import IST, STATUS_ACTIVE
from slbm_tracker.services.portfolio_service import PortfolioService
from slbm_tracker.services.scheduler_service import is_market_hours

FAMILY_ORDER = {"B": 0, "A": 1, "SPECIAL": 2}


def _utcnow() -> datetime.datetime:
    """Naive UTC, the storage convention for snapshot timestamps."""
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _ist_date(naive_utc: datetime.datetime) -> datetime.date:
    return naive_utc.replace(tzinfo=datetime.timezone.utc).astimezone(IST).date()


def start_of_ist_day(naive_utc: datetime.datetime) -> datetime.datetime:
    """Midnight IST of the given moment's IST date, as naive UTC."""
    day = _ist_date(naive_utc)
    start = datetime.datetime.combine(day, datetime.time(0, 0), tzinfo=IST)
    return start.astimezone(datetime.timezone.utc).replace(tzinfo=None)


def sweep_estimate_warning(polled: int) -> Optional[str]:
    """The overrun warning from configuration alone, before any sweep has run."""
    refresh = int(business_config.effective("live_refresh_seconds"))
    gap = float(get_settings().nse_rate_limit_delay_seconds)
    estimate = polled * gap
    if estimate > refresh:
        return (
            f"A full sweep of {polled} symbols takes about {estimate:.0f} s at the "
            f"{gap:g} s gap, longer than live_refresh_seconds ({refresh} s)."
        )
    return None


def _latest_series(db: Session, symbols: list[str], today: datetime.date) -> dict[str, list[SLBSnapshot]]:
    """Latest quote snapshot per (symbol, series code) for unexpired series."""
    if not symbols:
        return {}
    max_ts = (
        db.query(
            SLBSnapshot.symbol.label("symbol"),
            SLBSnapshot.series_code.label("series_code"),
            func.max(SLBSnapshot.timestamp).label("max_ts"),
        )
        .filter(
            SLBSnapshot.symbol.in_(symbols),
            SLBSnapshot.series_code.isnot(None),
            SLBSnapshot.expiry_date >= today,
        )
        .group_by(SLBSnapshot.symbol, SLBSnapshot.series_code)
        .subquery()
    )
    max_id = (
        db.query(func.max(SLBSnapshot.id).label("max_id"))
        .join(
            max_ts,
            (SLBSnapshot.symbol == max_ts.c.symbol)
            & (SLBSnapshot.series_code == max_ts.c.series_code)
            & (SLBSnapshot.timestamp == max_ts.c.max_ts),
        )
        .group_by(SLBSnapshot.symbol, SLBSnapshot.series_code)
        .subquery()
    )
    out: dict[str, list[SLBSnapshot]] = {}
    for snap in db.query(SLBSnapshot).join(max_id, SLBSnapshot.id == max_id.c.max_id).all():
        out.setdefault(snap.symbol, []).append(snap)
    return out


def _last_positive_fees(db: Session, symbols: list[str], today: datetime.date) -> dict:
    """Retain a reported last fee when the session-only month feed sends zero.

    Match the exact settlement as well as code: XD in a different year must
    never supply the current contract's fee. These are observation timestamps,
    not execution timestamps.
    """
    if not symbols:
        return {}
    settlement = func.coalesce(SLBSnapshot.settlement_date, SLBSnapshot.expiry_date)
    latest = (
        db.query(SLBSnapshot.symbol.label('symbol'), SLBSnapshot.series_code.label('code'),
                 settlement.label('settlement'), func.max(SLBSnapshot.timestamp).label('at'))
        .filter(SLBSnapshot.symbol.in_(symbols), SLBSnapshot.series_family == 'B',
                SLBSnapshot.ltp > 0, SLBSnapshot.expiry_date >= today)
        .group_by(SLBSnapshot.symbol, SLBSnapshot.series_code, settlement).subquery()
    )
    rows = (db.query(SLBSnapshot).join(latest,
        (SLBSnapshot.symbol == latest.c.symbol) & (SLBSnapshot.series_code == latest.c.code)
        & (settlement == latest.c.settlement) & (SLBSnapshot.timestamp == latest.c.at))
        .order_by(SLBSnapshot.id.desc()).all())
    fees = {}
    for row in rows:
        fees.setdefault((row.symbol, row.series_code, row.settlement_date or row.expiry_date), row)
    return fees


def _series_row(snap: SLBSnapshot, now: datetime.datetime, lendable: int = 0, spot_fallback: float = 0, fee_fallback: Optional[SLBSnapshot] = None) -> dict[str, Any]:
    stale = now-snap.timestamp>datetime.timedelta(seconds=get_settings().opportunity_quote_max_age_seconds)
    fillable = max(0, min(lendable, int(snap.best_bid_qty or 0))) if not stale else 0
    spot = spot_fallback or snap.underlying_price
    economics = lending_economics.evaluate(fillable, snap.best_bid, spot,
        max(((snap.settlement_date or snap.expiry_date) - _ist_date(now)).days, 1)) if fillable and snap.best_bid else None
    return {
        "is_stale": stale,
        "underlying_price": spot or None,
        "fillable": fillable,
        "net_fee": float(economics.net_fee) if economics else None,
        "net_yield_pa": float(economics.net_yield_pa) if economics and economics.net_yield_pa is not None else None,
        "series_code": snap.series_code,
        "series_family": snap.series_family,
        "series": snap.series,
        "settlement_date": snap.settlement_date or snap.expiry_date,
        "settlement_estimated": snap.settlement_estimated,
        "ltp": snap.ltp if snap.ltp and snap.ltp > 0 else (fee_fallback.ltp if fee_fallback else snap.ltp),
        "fee_from_previous_observation": bool(not snap.ltp and fee_fallback),
        "fee_observed_at": fee_fallback.timestamp if not snap.ltp and fee_fallback else snap.timestamp,
        "traded_quantity": snap.volume if _ist_date(snap.timestamp)==_ist_date(now) else 0,
        "open_positions": snap.open_positions,
        "best_bid": snap.best_bid,
        "best_bid_qty": snap.best_bid_qty,
        "best_ask": snap.best_ask,
        "best_ask_qty": snap.best_ask_qty,
        "annualized_yield": snap.annualized_yield,
        "updated_at": snap.timestamp,
        "age_seconds": max(0, int((now - snap.timestamp).total_seconds())),
    }


def build_board(db: Session, sweep_status: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Every holding (held or lent) with all its live series, Series B first.

    Holdings NSE reported as not on the SLB board come last. Each series
    carries its age, so outside market hours the last values show how old
    they are.
    """
    now = _utcnow()
    today = _ist_date(now)
    lent_ids = db.query(LendingContract.stock_id).filter(LendingContract.status == STATUS_ACTIVE)
    stocks = (
        db.query(Stock)
        .filter(or_(Stock.quantity > 0, Stock.id.in_(lent_ids)))
        .order_by(Stock.symbol.asc())
        .all()
    )
    positions = PortfolioService.holding_positions(db, stocks)
    series = _latest_series(db, [s.symbol for s in stocks], today)
    fees = _last_positive_fees(db, [s.symbol for s in stocks], today)

    import json
    from pathlib import Path
    try: names=json.loads((Path(get_settings().history_cache_dir)/"names.json").read_text())
    except (ValueError,OSError): names={}
    equity_quotes=equity_quote_service.read_quotes()
    holdings = []
    for stock in stocks:
        pos = positions[stock.id]
        snapshots = series.get(stock.symbol, [])
        spot_snap=next((s for s in sorted(snapshots,key=lambda r:r.timestamp,reverse=True) if s.underlying_price),None)
        spot_fallback=spot_snap.underlying_price if spot_snap else 0
        spot_at=spot_snap.timestamp if spot_snap else None
        spot_source='NSE SLB feed'
        quote=equity_quotes.get(stock.symbol)
        # An independent quote is only used while recent; its time remains visible.
        if quote:
            try:
                quote_at=datetime.datetime.fromisoformat(quote['timestamp']).astimezone(datetime.timezone.utc).replace(tzinfo=None)
                if now-quote_at>datetime.timedelta(hours=24): quote=None
            except (ValueError,KeyError):quote=None
        if quote and (spot_at is None or quote_at>=spot_at):
            spot_fallback=quote['price'];spot_at=quote_at;spot_source=quote['source']
        rows = [_series_row(s, now, pos.lendable, spot_fallback, fees.get((s.symbol, s.series_code, s.settlement_date or s.expiry_date))) for s in snapshots]
        for row,snap in zip(rows,snapshots):
            row['underlying_updated_at']=spot_at
            row['underlying_source']=spot_source
        rows.sort(
            key=lambda r: (
                FAMILY_ORDER.get(r["series_family"], 3),
                r["settlement_date"] or datetime.date.max,
                r["series_code"],
            )
        )
        if not stock.is_slb_eligible:
            status = "not_on_slb"
        elif rows:
            status = "live"
        else:
            status = "no_data"
        pos = positions[stock.id]
        holdings.append(
            {
                "stock_id": stock.id,
                "symbol": stock.symbol,
                "name": stock.name or names.get(stock.symbol),
                "lendable": pos.lendable,
                "total_owned": pos.total_owned,
                "in_demat": pos.in_demat,
                "lent": pos.lent,
                "status": status,
                "slb_checked_at": stock.slb_checked_at,
                "series": rows,
            }
        )
    holdings.sort(key=lambda h: (h["status"] == "not_on_slb", h["symbol"]))

    polled = len(PortfolioService.polled_symbols(db, now=now))
    warning = (sweep_status or {}).get("warning") if sweep_status else sweep_estimate_warning(polled)
    return {
        "generated_at": now,
        "market_open": is_market_hours(now.replace(tzinfo=datetime.timezone.utc)),
        "refresh_seconds": int(business_config.effective("live_refresh_seconds")),
        "series_family": get_settings().series_family,
        "last_sweep": sweep_status,
        "sweep_warning": warning,
        "holdings": holdings,
    }


def trade_events(db: Session, since: Optional[datetime.datetime] = None, limit: int = 500) -> dict[str, Any]:
    """Inferred trade events since ``since`` (naive UTC; default: start of today IST), newest first."""
    since = since or start_of_ist_day(_utcnow())
    rows = (
        db.query(SLBTradeEvent)
        .filter(SLBTradeEvent.occurred_at >= since)
        .order_by(SLBTradeEvent.occurred_at.desc(), SLBTradeEvent.id.desc())
        .limit(limit)
        .all()
    )
    return {
        "since": since,
        "inferred": True,
        "events": [
            {
                "id": e.id,
                "inferred": True,
                "symbol": e.symbol,
                "series_code": e.series_code,
                "series_family": e.series_family,
                "series": e.series,
                "settlement_date": e.settlement_date,
                "quantity": e.quantity,
                "last_fee": e.last_fee,
                "traded_quantity_total": e.traded_quantity_total,
                "occurred_at": e.occurred_at,
            }
            for e in rows
        ],
    }
