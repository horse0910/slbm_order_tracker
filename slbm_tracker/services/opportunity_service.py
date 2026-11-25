"""Lending opportunities at the live Series B bid, net of charges (Phase 3.3).

For each holding × the next ``tracked_expiries`` Series B expiries (from the
stored quote snapshots; this module never calls NSE):

* fillable = min(lendable, best bid quantity), lendable = in demat − pledged
* net fee and net annualised yield at the best bid, via ``lending_economics``

An option is actionable only when there is a live bid, the fill reaches
``min_fill_qty``, the net yield clears ``min_net_yield_pct``, the net fee is
above zero, no corporate action falls inside the lending period, and the quote
is under 24 hours old. Otherwise ``reasons`` says why. No order-value check
(decided 4 Oct 2026). Each actionable option carries a paste-ready line for a
Zerodha support ticket (Phase 3.7).
"""

from __future__ import annotations

import datetime
import json
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from slbm_tracker.config import get_settings
from slbm_tracker.db.models import SLBSnapshot, Stock, StockPriceHistory
from slbm_tracker.services import business_config, lending_economics
from slbm_tracker.services.contract_service import today_ist
from slbm_tracker.services.portfolio_service import PortfolioService
from slbm_tracker.utils.money import money_to_float, to_decimal

QUOTE_MAX_AGE = datetime.timedelta(hours=24)


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def last_price(db: Session, symbol: str) -> Optional[float]:
    """Latest known share price: newest snapshot's underlying, else stored daily close.

    NSE sends underLyingLtp = 0 on some series; zero means "not given", so only
    prices above zero count.
    """
    from slbm_tracker.services.equity_quote_service import read_quotes
    quote=read_quotes().get(symbol)
    if quote:
        try:
            observed=datetime.datetime.fromisoformat(quote['timestamp']).astimezone(datetime.timezone.utc).replace(tzinfo=None)
            if _utcnow()-observed<=QUOTE_MAX_AGE and quote['price']>0:return quote['price']
        except (ValueError,KeyError):pass
    snap = (
        db.query(SLBSnapshot.underlying_price)
        .filter(SLBSnapshot.symbol == symbol, SLBSnapshot.underlying_price > 0)
        .order_by(SLBSnapshot.timestamp.desc(), SLBSnapshot.id.desc())
        .first()
    )
    if snap is not None:
        return snap[0]
    bar = (
        db.query(StockPriceHistory.close)
        .filter(StockPriceHistory.symbol == symbol, StockPriceHistory.close > 0)
        .order_by(StockPriceHistory.date.desc())
        .first()
    )
    return bar[0] if bar is not None else None


def ticket_line(symbol: str, qty: int, code: str, bid: Decimal) -> str:
    """Paste-ready line for a Zerodha support ticket, e.g. 'IREDA | lend 4000 | XD | ₹3.10/share'."""
    return f"{symbol} | lend {qty} | {code} | ₹{bid:.2f}/share"


def _latest_series_b(db: Session, symbols: list[str], after: datetime.date) -> dict[str, list[SLBSnapshot]]:
    """Latest Series B snapshot per (symbol, series code) settling after ``after``."""
    if not symbols:
        return {}
    family = get_settings().series_family
    max_ts = (
        db.query(
            SLBSnapshot.symbol.label("symbol"),
            SLBSnapshot.series_code.label("series_code"),
            func.max(SLBSnapshot.timestamp).label("max_ts"),
        )
        .filter(
            SLBSnapshot.symbol.in_(symbols),
            SLBSnapshot.series_family == family,
            SLBSnapshot.series_code.isnot(None),
            SLBSnapshot.expiry_date > after,
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
    for rows in out.values():
        rows.sort(key=lambda s: (s.settlement_date or s.expiry_date, s.series_code))
    return out


def _corporate_action(snap: SLBSnapshot) -> tuple[Optional[str], Optional[datetime.date]]:
    try:
        extra = json.loads(snap.raw_data) if snap.raw_data else {}
    except (ValueError, TypeError):
        extra = {}
    purpose = (extra.get("corporate_action") or "").strip() or None
    ex_raw = extra.get("corporate_action_ex_date")
    try:
        ex_date = datetime.date.fromisoformat(ex_raw) if ex_raw else None
    except ValueError:
        ex_date = None
    return purpose, ex_date


def _option(
    snap: SLBSnapshot,
    symbol: str,
    lendable: int,
    spot_fallback: Optional[float],
    today: datetime.date,
    now: datetime.datetime,
    floor: Decimal,
    min_fill: int,
    charges: lending_economics.Charges,
    min_net_fee: Decimal = Decimal(0),
) -> dict[str, Any]:
    settlement = snap.settlement_date or snap.expiry_date
    days = max((settlement - today).days, 1)
    bid = to_decimal(snap.best_bid)
    bid_qty = int(snap.best_bid_qty or 0)
    fillable = max(min(lendable, bid_qty), 0)
    spot = to_decimal(snap.underlying_price) if (snap.underlying_price or 0) > 0 else to_decimal(spot_fallback)
    economics = (
        lending_economics.evaluate(fillable, bid, spot, days, charges) if fillable > 0 and bid > 0 else None
    )
    age = now - snap.timestamp
    purpose, ex_date = _corporate_action(snap)

    reasons: list[str] = []
    if bid <= 0 or bid_qty <= 0:
        reasons.append("no live bid")
    if lendable <= 0:
        reasons.append("nothing lendable (all lent or pledged)")
    elif fillable < min_fill:
        reasons.append(f"fillable {fillable} is below the minimum of {min_fill}")
    if economics is not None:
        if economics.net_fee <= 0:
            reasons.append("charges exceed the fee")
        elif min_net_fee > 0 and economics.net_fee < min_net_fee:
            reasons.append(f"net fee below ₹{min_net_fee:,.2f}")
        if economics.net_yield_pa is None:
            reasons.append("no share price to compute the yield")
        elif economics.net_yield_pa < floor:
            reasons.append(f"net yield {economics.net_yield_pa}% is below the {floor:.2f}% floor")
    if purpose and (ex_date is None or today <= ex_date <= settlement):
        reasons.append(f"corporate action: {purpose}" + (f" (ex {ex_date.isoformat()})" if ex_date else ""))
    max_age=get_settings().opportunity_quote_max_age_seconds
    if age > datetime.timedelta(seconds=max_age):
        reasons.append("quote older than 24 hours" if max_age==86400 else f"quote older than {max_age//60} minutes")

    actionable = not reasons
    price_for_floor = (
        lending_economics.price_for_target_yield(fillable or lendable, spot, days, floor, charges)
        if (fillable or lendable) > 0 and spot > 0
        else None
    )
    return {
        "series_code": snap.series_code,
        "series": snap.series,
        "settlement_date": settlement,
        "settlement_estimated": snap.settlement_estimated,
        "days": days,
        "days_to_settlement": days,
        "best_bid": float(bid),
        "best_bid_qty": bid_qty,
        "best_ask": snap.best_ask,
        "spot": spot if spot > 0 else None,
        "lendable": lendable,
        "fillable": fillable,
        "gross_fee": economics.gross_fee if economics else None,
        "net_fee": economics.net_fee if economics else None,
        "gross_yield_pa": economics.gross_yield_pa if economics else None,
        "net_yield_pa": economics.net_yield_pa if economics else None,
        "breakeven_price": economics.breakeven_price if economics else None,
        "price_for_floor": price_for_floor,
        "corporate_action": purpose,
        "corporate_action_ex_date": ex_date,
        "quote_at": snap.timestamp,
        "quote_age_seconds": max(int(age.total_seconds()), 0),
        "actionable": actionable,
        "reasons": reasons,
        "ticket": ticket_line(symbol, fillable, snap.series_code, bid) if actionable else None,
    }


def build(
    db: Session,
    *,
    expiries: Optional[int] = None,
    today: Optional[datetime.date] = None,
    now: Optional[datetime.datetime] = None,
    min_net_yield_pct: Optional[float] = None,
    min_fill_qty: Optional[int] = None,
    symbol: Optional[str] = None,
    min_net_fee_inr: Optional[float] = None,
) -> dict[str, Any]:
    """Every held stock × its next Series B expiries, with Decimal money values."""
    today = today or today_ist()
    now = now or _utcnow()
    n = int(expiries or business_config.effective("tracked_expiries"))
    floor = to_decimal(
        min_net_yield_pct if min_net_yield_pct is not None else business_config.effective("min_net_yield_pct")
    )
    min_fill = int(min_fill_qty if min_fill_qty is not None else business_config.effective("min_fill_qty"))
    charges = lending_economics.Charges.from_config()
    min_net_fee = to_decimal(
        min_net_fee_inr if min_net_fee_inr is not None else business_config.effective("min_net_fee_inr")
    )

    query = db.query(Stock).filter(Stock.quantity > 0)
    if symbol:
        query = query.filter(Stock.symbol == symbol.strip().upper())
    stocks = query.order_by(Stock.symbol.asc()).all()
    positions = PortfolioService.holding_positions(db, stocks)
    series = _latest_series_b(db, [s.symbol for s in stocks], today)

    out = []
    for stock in stocks:
        snaps = series.get(stock.symbol, [])[:n]
        if not snaps:
            continue
        pos = positions[stock.id]
        fallback = last_price(db, stock.symbol)
        options = [
            _option(s, stock.symbol, pos.lendable, fallback, today, now, floor, min_fill, charges, min_net_fee)
            for s in snaps
        ]
        out.append(
            {
                "symbol": stock.symbol,
                "stock_id": stock.id,
                "in_demat": pos.in_demat,
                "pledged": pos.pledged,
                "lendable": pos.lendable,
                "options": options,
            }
        )
    return {
        "generated_at": now,
        "expiries": n,
        "min_net_yield_pct": float(floor),
        "min_fill_qty": min_fill,
        "min_net_fee_inr": float(min_net_fee),
        "series_family": get_settings().series_family,
        "stocks": out,
    }


def actionable_options(board: dict[str, Any]) -> list[dict[str, Any]]:
    """Every actionable option across stocks, best net yield first."""
    rows = [
        {**opt, "symbol": s["symbol"], "stock_id": s["stock_id"]}
        for s in board["stocks"]
        for opt in s["options"]
        if opt["actionable"]
    ]
    rows.sort(key=lambda o: o["net_yield_pa"] or Decimal(0), reverse=True)
    return rows


def to_json(board: dict[str, Any]) -> dict[str, Any]:
    """The board with Decimals as floats for the API."""

    def conv(value: Any) -> Any:
        if isinstance(value, Decimal):
            return money_to_float(value) if value.as_tuple().exponent == -2 else float(value)
        if isinstance(value, dict):
            return {k: conv(v) for k, v in value.items()}
        if isinstance(value, list):
            return [conv(v) for v in value]
        return value

    return conv(board)
