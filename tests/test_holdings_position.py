"""In demat / lent / total owned per stock (house rule 5; plan 1.5-1.7).

Zerodha's holdings export leaves lent shares out, but only from the T+1
forward-leg settlement: an export taken on the trade day still includes them.
So in demat = imported quantity minus ACTIVE contracts whose trade_date is on
or after the IST date of the last import; lent = all ACTIVE contracts.
"""

from __future__ import annotations

import datetime

from sqlalchemy.orm import Session

from slbm_tracker.db.models import LendingContract, Stock
from slbm_tracker.services.portfolio_service import PortfolioService

T = datetime.date(2026, 10, 1)


def _utc(y, m, d, hh, mm) -> datetime.datetime:
    """Naive UTC, the storage convention for holdings_imported_at."""
    return datetime.datetime(y, m, d, hh, mm)


def _stock(db: Session, qty: int, imported_at: datetime.datetime | None) -> Stock:
    stock = Stock(symbol="SBIN", quantity=qty, holdings_imported_at=imported_at)
    db.add(stock)
    db.commit()
    return stock


def _lend(db: Session, stock: Stock, qty: int, trade_date: datetime.date, status: str = "ACTIVE") -> LendingContract:
    c = LendingContract(stock_id=stock.id, series="XN", expiry_date=datetime.date(2026, 11, 3),
                        quantity_lent=qty, lending_fee=1.0, status=status, trade_date=trade_date)
    db.add(c)
    db.commit()
    return c


def _triple(db: Session, stock: Stock) -> tuple[int, int, int]:
    p = PortfolioService.holding_position(db, stock)
    return p.in_demat, p.lent, p.total_owned


def test_same_day_export_still_includes_the_lent_shares(db: Session):
    # Lend 200 on T, import on the evening of T (19:00 IST) showing 500.
    stock = _stock(db, 0, None)
    _lend(db, stock, 200, T)
    stock.quantity = 500
    stock.holdings_imported_at = _utc(2026, 10, 1, 13, 30)
    db.commit()
    assert _triple(db, stock) == (300, 200, 500)


def test_next_day_export_already_excludes_them(db: Session):
    stock = _stock(db, 500, _utc(2026, 10, 1, 13, 30))
    _lend(db, stock, 200, T)
    # Re-import on T+1 showing 300: the contract is no longer subtracted.
    stock.quantity = 300
    stock.holdings_imported_at = _utc(2026, 10, 2, 4, 0)
    db.commit()
    assert _triple(db, stock) == (300, 200, 500)


def test_contract_traded_before_the_import_date_is_never_subtracted(db: Session):
    stock = _stock(db, 300, _utc(2026, 10, 5, 6, 0))
    _lend(db, stock, 200, datetime.date(2026, 10, 2))
    assert _triple(db, stock) == (300, 200, 500)


def test_import_just_after_midnight_ist_is_the_new_day(db: Session):
    # 18:35 UTC on 1 Oct is 00:05 IST on 2 Oct.
    stock = _stock(db, 300, _utc(2026, 10, 1, 18, 35))
    _lend(db, stock, 200, datetime.date(2026, 10, 1))  # before the IST import date
    assert _triple(db, stock) == (300, 200, 500)
    _lend(db, stock, 50, datetime.date(2026, 10, 2))   # on the IST import date
    assert _triple(db, stock) == (250, 250, 500)


def test_only_active_contracts_count(db: Session):
    stock = _stock(db, 500, _utc(2026, 10, 1, 4, 0))
    _lend(db, stock, 100, T, status="CLOSED")
    _lend(db, stock, 100, T, status="RECALLED")
    assert _triple(db, stock) == (500, 0, 500)


def test_never_imported_subtracts_every_active_contract(db: Session):
    stock = _stock(db, 500, None)
    _lend(db, stock, 200, datetime.date(2026, 9, 1))
    assert _triple(db, stock) == (300, 200, 500)


def test_positions_for_many_stocks_match_single_lookups(db: Session):
    a = Stock(symbol="AAA", quantity=100, holdings_imported_at=_utc(2026, 10, 1, 4, 0))
    b = Stock(symbol="BBB", quantity=0, holdings_imported_at=_utc(2026, 10, 2, 4, 0))
    db.add_all([a, b])
    db.commit()
    _lend(db, a, 40, T)
    _lend(db, b, 70, T)  # traded before BBB's import: fully lent, missing from the file
    positions = PortfolioService.holding_positions(db, [a, b])
    assert (positions[a.id].in_demat, positions[a.id].lent, positions[a.id].total_owned) == (60, 40, 100)
    assert (positions[b.id].in_demat, positions[b.id].lent, positions[b.id].total_owned) == (0, 70, 70)
    assert [c.quantity_lent for c in positions[b.id].active_contracts] == [70]
