"""Phase 4.4: rollover advisor priced at the next Series B bid, net of charges.

Options are later Series B series; each is priced at its best bid on the
fillable quantity (the contract's shares, capped by the bid quantity) with
lending_economics. SEBI's 365-day cap from origin_date marks options past it;
when none fit, the status is BLOCKED_BY_CAP. 'N of 365 days used' is reported.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from slbm_tracker.db.models import LendingContract, SLBSnapshot, Stock
from slbm_tracker.services.rollover_service import RolloverService

TODAY = datetime.date(2026, 10, 30)
TS = datetime.datetime(2026, 10, 30, 6, 0)


def _setup(db: Session, origin=datetime.date(2026, 10, 1), expiry=datetime.date(2026, 11, 3)) -> LendingContract:
    stock = Stock(symbol="IRFC", quantity=1000)
    db.add(stock)
    db.flush()
    c = LendingContract(stock_id=stock.id, series="Nov-2026", series_code="XN", expiry_date=expiry,
                        reverse_leg_date=expiry, quantity_lent=1000, lending_fee=0.35, status="ACTIVE",
                        trade_date=origin, origin_date=origin)
    db.add(c)
    db.commit()
    return c


def _snap(db: Session, code: str, settles: datetime.date, bid: float, bid_qty: int, ltp: float,
          family: str = "B", underlying: float = 77.06) -> None:
    db.add(SLBSnapshot(symbol="IRFC", series=settles.strftime("%b-%Y"), series_code=code, series_family=family,
                       expiry_date=settles, settlement_date=settles, best_bid=bid, best_bid_qty=bid_qty,
                       best_ask=bid + 0.1, ltp=ltp, underlying_price=underlying, annualized_yield=None,
                       timestamp=TS, source="NSE_QUOTE"))
    db.commit()


def test_options_are_priced_at_the_bid_not_the_last_traded_fee(db: Session):
    _setup(db)
    _snap(db, "XD", datetime.date(2026, 12, 1), bid=1.17, bid_qty=1376, ltp=33.0)  # stale ltp far above the bid
    rec = RolloverService.advise(db, today=TODAY)[0]
    xd = rec.options[0]
    assert (xd.series_code, xd.best_bid, xd.fillable) == ("XD", 1.17, 1000)
    # 1000 x 1.17 = 1170 gross; net 878.54 over the 28 days from 3 Nov to 1 Dec
    assert xd.net_fee == Decimal("878.54")
    assert xd.net_yield_pa == Decimal("14.86")
    assert rec.fee_delta == 0.82  # bid 1.17 vs the current 0.35, not the 33.0 ltp
    assert rec.favorable is True


def test_fill_is_capped_by_the_bid_quantity(db: Session):
    _setup(db)
    _snap(db, "XD", datetime.date(2026, 12, 1), bid=1.17, bid_qty=400, ltp=1.17)
    assert RolloverService.advise(db, today=TODAY)[0].options[0].fillable == 400


def test_only_later_series_b_options(db: Session):
    _setup(db)
    _snap(db, "12", datetime.date(2026, 12, 1), bid=9.0, bid_qty=5000, ltp=9.0, family="A")
    _snap(db, "XN", datetime.date(2026, 11, 3), bid=0.4, bid_qty=5000, ltp=0.4)  # the current series
    _snap(db, "X1", datetime.date(2027, 1, 5), bid=1.5, bid_qty=5000, ltp=1.5)
    rec = RolloverService.advise(db, today=TODAY)[0]
    assert [o.series_code for o in rec.options] == ["X1"]


def test_days_used_of_365_and_options_past_the_cap(db: Session):
    origin = datetime.date(2025, 11, 10)
    _setup(db, origin=origin, expiry=datetime.date(2026, 11, 3))
    _snap(db, "X1", datetime.date(2027, 1, 5), bid=1.5, bid_qty=5000, ltp=1.5)   # 421 days from origin
    rec = RolloverService.advise(db, today=TODAY)[0]
    assert rec.days_used == (TODAY - origin).days == 354
    assert rec.days_used_label == "354 of 365 days used"
    assert rec.options[0].within_cap is False and rec.options[0].chain_days == 421
    assert rec.status == "BLOCKED_BY_CAP"
    assert rec.best_option is None
    assert "365" in rec.rationale


def test_roll_available_when_an_option_fits_the_cap(db: Session):
    _setup(db, origin=datetime.date(2026, 1, 5), expiry=datetime.date(2026, 11, 3))
    _snap(db, "XD", datetime.date(2026, 12, 1), bid=1.17, bid_qty=5000, ltp=1.17)  # 330 days
    _snap(db, "X1", datetime.date(2027, 1, 5), bid=3.0, bid_qty=5000, ltp=3.0)    # 365 days: still allowed
    _snap(db, "X2", datetime.date(2027, 2, 2), bid=9.0, bid_qty=5000, ltp=9.0)    # 393 days: blocked
    rec = RolloverService.advise(db, today=TODAY)[0]
    assert rec.status == "ROLL_AVAILABLE"
    assert rec.best_option.series_code == "X1"  # best net yield within the cap
    assert {o.series_code: o.within_cap for o in rec.options} == {"X1": True, "XD": True, "X2": False}
