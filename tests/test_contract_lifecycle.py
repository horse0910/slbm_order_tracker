"""Phase 4.2: close, recall, roll (365-day cap from origin) and auto-close.

P&L is booked through A's idempotent PnLService.record_for_contract (B's
calculate_contract_pnl is not ported). Closing, recalling or rolling keeps the
Phase 1 figures right: lent drops, and the shares come back into 'in demat'.
"""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy.orm import Session

from slbm_tracker.db.models import LendingContract, PnLRecord, Stock
from slbm_tracker.services.contract_service import ContractService
from slbm_tracker.services.portfolio_service import PortfolioService

OCT_1 = datetime.date(2026, 10, 1)
IMPORT_OCT_1 = datetime.datetime(2026, 10, 1, 4, 0)  # 09:30 IST


def _stock(db: Session, qty: int = 500, imported_at=IMPORT_OCT_1) -> Stock:
    stock = Stock(symbol="SBIN", quantity=qty, holdings_imported_at=imported_at)
    db.add(stock)
    db.commit()
    return stock


def _lend(db: Session, stock: Stock, qty: int = 300, trade_date=OCT_1, expiry=datetime.date(2026, 11, 3)):
    return ContractService.create_contract(
        db=db, stock_id=stock.id, expiry_date=expiry, reverse_leg_date=expiry, quantity_lent=qty,
        lending_fee=1.0, series="Nov-2026", series_code="XN", trade_date=trade_date,
    )


def _triple(db: Session, stock: Stock) -> tuple[int, int, int]:
    p = PortfolioService.holding_position(db, stock)
    return p.in_demat, p.lent, p.lendable


# --- close -------------------------------------------------------------------


def test_closing_books_pnl_once_and_closing_again_changes_nothing(db: Session):
    c = _lend(db, _stock(db))
    ContractService.close_contract(db, c.id, today=datetime.date(2026, 11, 4))
    first = db.query(PnLRecord).one()
    assert (c.status, c.closed_on) == ("CLOSED", datetime.date(2026, 11, 4))
    assert first.gross_fee == 300.0
    ContractService.close_contract(db, c.id, today=datetime.date(2026, 11, 9))
    assert db.query(PnLRecord).count() == 1
    assert db.query(PnLRecord).one().id == first.id
    assert c.closed_on == datetime.date(2026, 11, 4)  # unchanged


def test_lent_drops_and_in_demat_rises_when_a_contract_closes(db: Session):
    stock = _stock(db)
    c = _lend(db, stock)
    assert _triple(db, stock) == (200, 300, 200)
    ContractService.close_contract(db, c.id, today=datetime.date(2026, 11, 4))
    assert _triple(db, stock) == (500, 0, 500)


def test_contract_lent_before_the_import_returns_to_demat_when_it_closes(db: Session):
    stock = _stock(db)
    c = _lend(db, stock)  # 300 lent on 1 Oct
    # 2 Oct export leaves the 300 out: 200 in demat.
    stock.quantity, stock.holdings_imported_at = 200, datetime.datetime(2026, 10, 2, 4, 0)
    db.commit()
    assert _triple(db, stock) == (200, 300, 200)
    ContractService.close_contract(db, c.id, today=datetime.date(2026, 11, 4))
    assert _triple(db, stock) == (500, 0, 500)  # back in demat, not lost
    # The next export shows all 500: nothing is added twice.
    stock.quantity, stock.holdings_imported_at = 500, datetime.datetime(2026, 11, 5, 4, 0)
    db.commit()
    assert _triple(db, stock) == (500, 0, 500)


# --- recall ------------------------------------------------------------------


def test_recall_sets_recalled_and_returns_the_shares(db: Session):
    stock = _stock(db)
    c = _lend(db, stock)
    ContractService.recall_contract(db, c.id, today=datetime.date(2026, 10, 10))
    assert (c.status, c.closed_on) == ("RECALLED", datetime.date(2026, 10, 10))
    assert db.query(PnLRecord).count() == 0
    ContractService.recall_contract(db, c.id, today=datetime.date(2026, 10, 11))  # idempotent
    assert c.closed_on == datetime.date(2026, 10, 10)
    assert _triple(db, stock) == (500, 0, 500)
    with pytest.raises(ValueError, match="RECALLED"):
        ContractService.close_contract(db, c.id, today=datetime.date(2026, 11, 4))


# --- roll --------------------------------------------------------------------


def _roll(db: Session, contract_id: int, settles: datetime.date, today: datetime.date, **kw):
    return ContractService.rollover_contract(
        db, contract_id, new_expiry_date=settles, new_reverse_leg_date=settles, new_lending_fee=1.2,
        new_series="Dec-2026", new_series_code="XD", today=today, **kw,
    )


def test_roll_marks_the_old_rolled_over_and_opens_a_new_active_one(db: Session):
    stock = _stock(db)
    old = _lend(db, stock)
    new = _roll(db, old.id, datetime.date(2026, 12, 1), today=datetime.date(2026, 11, 2))
    assert (old.status, old.closed_on) == ("ROLLED_OVER", datetime.date(2026, 11, 2))
    assert (new.status, new.rolled_from_id, new.origin_date) == ("ACTIVE", old.id, OCT_1)
    assert (new.quantity_lent, new.series_code, new.trade_date) == (300, "XD", datetime.date(2026, 11, 2))
    assert db.query(PnLRecord).filter_by(contract_id=old.id).count() == 1  # old leg's fee booked
    assert _triple(db, stock) == (200, 300, 200)  # still lent, nothing double-counted


def test_three_rolls_in_a_row_keep_the_original_origin_date(db: Session):
    c = _lend(db, _stock(db))
    for settles, today in [(datetime.date(2026, 12, 1), datetime.date(2026, 11, 2)),
                           (datetime.date(2027, 1, 5), datetime.date(2026, 11, 30)),
                           (datetime.date(2027, 2, 2), datetime.date(2027, 1, 4))]:
        c = _roll(db, c.id, settles, today=today)
    assert c.origin_date == OCT_1
    assert db.query(LendingContract).filter_by(status="ROLLED_OVER").count() == 3


def test_roll_ending_401_days_after_origin_is_refused(db: Session):
    c = _lend(db, _stock(db))
    with pytest.raises(ValueError, match="401 days"):
        _roll(db, c.id, OCT_1 + datetime.timedelta(days=401), today=datetime.date(2026, 11, 2))
    assert c.status == "ACTIVE"


def test_roll_at_day_360_that_would_end_at_day_395_is_refused(db: Session):
    c = _lend(db, _stock(db), expiry=OCT_1 + datetime.timedelta(days=362))
    with pytest.raises(ValueError, match="395 days.*365"):
        _roll(db, c.id, OCT_1 + datetime.timedelta(days=395), today=OCT_1 + datetime.timedelta(days=360))


def test_roll_ending_exactly_365_days_after_origin_is_allowed(db: Session):
    c = _lend(db, _stock(db))
    new = _roll(db, c.id, OCT_1 + datetime.timedelta(days=365), today=datetime.date(2026, 11, 2))
    assert new.status == "ACTIVE"


def test_roll_cannot_lend_more_than_the_old_contract(db: Session):
    c = _lend(db, _stock(db))
    with pytest.raises(ValueError, match="300"):
        _roll(db, c.id, datetime.date(2026, 12, 1), today=datetime.date(2026, 11, 2), quantity_lent=301)


def test_terminal_contracts_cannot_be_rolled(db: Session):
    c = _lend(db, _stock(db))
    ContractService.close_contract(db, c.id, today=datetime.date(2026, 11, 4))
    with pytest.raises(ValueError, match="CLOSED"):
        _roll(db, c.id, datetime.date(2026, 12, 1), today=datetime.date(2026, 11, 4))


# --- auto-close ----------------------------------------------------------------


def test_expired_contracts_auto_close_once(db: Session):
    stock = _stock(db)
    settled = _lend(db, stock, expiry=datetime.date(2026, 10, 6))
    open_ = _lend(db, stock, qty=100, expiry=datetime.date(2026, 11, 3))
    closed = ContractService.close_expired_contracts(db, today=datetime.date(2026, 10, 7))
    assert [c.id for c in closed] == [settled.id]
    assert (settled.status, settled.closed_on) == ("CLOSED", datetime.date(2026, 10, 6))
    assert open_.status == "ACTIVE"
    assert ContractService.close_expired_contracts(db, today=datetime.date(2026, 10, 8)) == []
    assert db.query(PnLRecord).count() == 1


def test_a_contract_settling_today_is_not_closed_yet(db: Session):
    c = _lend(db, _stock(db), expiry=datetime.date(2026, 10, 6))
    assert ContractService.close_expired_contracts(db, today=datetime.date(2026, 10, 6)) == []
    assert c.status == "ACTIVE"
