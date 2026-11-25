"""Unit tests for ContractService: creation, lifecycle, expiry boundaries.

Boundary conditions are exercised with an explicit ``today`` so they are
deterministic and independent of the machine clock / timezone.
"""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy.orm import Session

from slbm_tracker.db.models import LendingContract, Stock
from slbm_tracker.services.contract_service import (
    STATUS_ACTIVE,
    STATUS_EXPIRED,
    STATUS_EXPIRING_SOON,
    ContractService,
)
from slbm_tracker.services.portfolio_service import PortfolioService


def _make_stock(db: Session, symbol: str = "RELIANCE") -> Stock:
    stock = Stock(symbol=symbol, quantity=1000, avg_buy_price=2500.0)
    db.add(stock)
    db.commit()
    db.refresh(stock)
    return stock


def test_create_contract_success(db: Session):
    stock = _make_stock(db)
    contract = ContractService.create_contract(
        db=db,
        stock_id=stock.id,
        expiry_date=datetime.date(2026, 11, 3),
        quantity_lent=100,
        lending_fee=5.25,
        series="Nov-2026",
        trade_date=datetime.date(2026, 10, 1),
    )
    assert contract.id is not None
    assert contract.status == STATUS_ACTIVE
    assert contract.quantity_lent == 100
    assert contract.lending_fee == 5.25


def test_create_contract_requires_existing_stock(db: Session):
    with pytest.raises(ValueError, match="not found"):
        ContractService.create_contract(
            db=db,
            stock_id=99999,
            expiry_date=datetime.date(2026, 11, 3),
            quantity_lent=100,
            lending_fee=5.0,
        )


def test_create_contract_rejects_bad_quantity_and_dates(db: Session):
    stock = _make_stock(db)
    with pytest.raises(ValueError, match="quantity_lent must be a positive"):
        ContractService.create_contract(
            db=db, stock_id=stock.id, expiry_date=datetime.date(2026, 11, 3),
            quantity_lent=0, lending_fee=5.0,
        )
    with pytest.raises(ValueError, match="lending_fee .* non-negative"):
        ContractService.create_contract(
            db=db, stock_id=stock.id, expiry_date=datetime.date(2026, 11, 3),
            quantity_lent=10, lending_fee=-1.0,
        )
    with pytest.raises(ValueError, match="expiry_date cannot be before trade_date"):
        ContractService.create_contract(
            db=db, stock_id=stock.id, expiry_date=datetime.date(2026, 10, 1),
            quantity_lent=10, lending_fee=5.0, trade_date=datetime.date(2026, 10, 15),
        )


def test_lending_fee_income_uses_decimal_rounding(db: Session):
    stock = _make_stock(db)
    contract = ContractService.create_contract(
        db=db, stock_id=stock.id, expiry_date=datetime.date(2026, 11, 3),
        quantity_lent=333, lending_fee=1.115,  # 333 * 1.115 = 371.295 -> 371.30 (half-up)
        trade_date=datetime.date(2026, 10, 1),
    )
    view = ContractService.get_contract_view(db=db, contract_id=contract.id)
    assert view.lending_fee_income == 371.30


# --- Expiry boundary conditions (spec §2) --------------------------------

EXPIRY = datetime.date(2026, 11, 3)


def test_status_active_well_before_expiry(db: Session):
    # 30 days out, soon window default 7 -> ACTIVE
    status = ContractService.derive_status(
        STATUS_ACTIVE, EXPIRY, today=datetime.date(2026, 10, 4), soon_days=7
    )
    assert status == STATUS_ACTIVE


def test_status_expiring_soon_at_window_edge(db: Session):
    # Exactly soon_days (7) away -> EXPIRING_SOON (inclusive boundary)
    status = ContractService.derive_status(
        STATUS_ACTIVE, EXPIRY, today=datetime.date(2026, 10, 27), soon_days=7
    )
    assert status == STATUS_EXPIRING_SOON


def test_status_day_before_expiry_is_expiring_soon(db: Session):
    status = ContractService.derive_status(
        STATUS_ACTIVE, EXPIRY, today=datetime.date(2026, 11, 2), soon_days=7
    )
    assert status == STATUS_EXPIRING_SOON


def test_status_on_expiry_date_is_still_expiring_soon_not_expired(db: Session):
    # days_remaining == 0 on the expiry day: contract settles through the day,
    # so it is EXPIRING_SOON, NOT EXPIRED. This is the critical > vs >= boundary.
    status = ContractService.derive_status(
        STATUS_ACTIVE, EXPIRY, today=EXPIRY, soon_days=7
    )
    assert status == STATUS_EXPIRING_SOON


def test_status_day_after_expiry_is_expired(db: Session):
    status = ContractService.derive_status(
        STATUS_ACTIVE, EXPIRY, today=datetime.date(2026, 11, 4), soon_days=7
    )
    assert status == STATUS_EXPIRED


def test_terminal_status_never_overridden_by_dates(db: Session):
    # A CLOSED contract past expiry stays CLOSED, not EXPIRED.
    for terminal in ("RECALLED", "CLOSED", "ROLLED_OVER"):
        status = ContractService.derive_status(
            terminal, EXPIRY, today=datetime.date(2027, 1, 1), soon_days=7
        )
        assert status == terminal


def test_days_remaining_is_signed(db: Session):
    stock = _make_stock(db)
    contract = ContractService.create_contract(
        db=db, stock_id=stock.id, expiry_date=EXPIRY, quantity_lent=10,
        lending_fee=5.0, trade_date=datetime.date(2026, 10, 1),
    )
    view_before = ContractService.to_view(contract, "RELIANCE", today=datetime.date(2026, 10, 24))
    assert view_before.days_remaining == 10
    view_after = ContractService.to_view(contract, "RELIANCE", today=datetime.date(2026, 11, 10))
    assert view_after.days_remaining == -7
    assert view_after.effective_status == STATUS_EXPIRED


def test_tenure_days(db: Session):
    stock = _make_stock(db)
    contract = ContractService.create_contract(
        db=db, stock_id=stock.id, expiry_date=datetime.date(2026, 11, 3),
        quantity_lent=10, lending_fee=5.0, trade_date=datetime.date(2026, 10, 1),
    )
    view = ContractService.get_contract_view(db=db, contract_id=contract.id)
    assert view.tenure_days == 33


def test_reconcile_expired_is_idempotent(db: Session):
    stock = _make_stock(db)
    ContractService.create_contract(
        db=db, stock_id=stock.id, expiry_date=datetime.date(2026, 10, 6),
        quantity_lent=10, lending_fee=5.0, trade_date=datetime.date(2026, 9, 1),
    )
    today = datetime.date(2026, 10, 20)
    first = ContractService.reconcile_expired(db=db, today=today)
    second = ContractService.reconcile_expired(db=db, today=today)
    assert first == 1
    assert second == 0
    row = db.query(LendingContract).first()
    assert row.status == STATUS_EXPIRED


def test_reconcile_does_not_touch_terminal_or_future(db: Session):
    stock = _make_stock(db)
    # future ACTIVE
    ContractService.create_contract(
        db=db, stock_id=stock.id, expiry_date=datetime.date(2026, 12, 1),
        quantity_lent=10, lending_fee=5.0, trade_date=datetime.date(2026, 10, 1),
    )
    # past but CLOSED (terminal)
    closed = LendingContract(
        stock_id=stock.id, expiry_date=datetime.date(2026, 9, 1), quantity_lent=5,
        lending_fee=4.0, status="CLOSED", trade_date=datetime.date(2026, 8, 1),
    )
    db.add(closed)
    db.commit()
    changed = ContractService.reconcile_expired(db=db, today=datetime.date(2026, 10, 20))
    assert changed == 0


def test_list_filter_by_effective_status(db: Session):
    stock = _make_stock(db)
    # One expiring soon, one far future — filtered via derived status.
    ContractService.create_contract(
        db=db, stock_id=stock.id, expiry_date=datetime.date(2026, 10, 24),
        quantity_lent=10, lending_fee=5.0, trade_date=datetime.date(2026, 10, 1),
    )
    ContractService.create_contract(
        db=db, stock_id=stock.id, expiry_date=datetime.date(2027, 3, 2),
        quantity_lent=10, lending_fee=5.0, trade_date=datetime.date(2026, 10, 1),
    )
    today = datetime.date(2026, 10, 20)
    soon = ContractService.list_contracts(db=db, effective_status=STATUS_EXPIRING_SOON, today=today)
    active = ContractService.list_contracts(db=db, effective_status=STATUS_ACTIVE, today=today)
    assert len(soon) == 1
    assert len(active) == 1
    assert soon[0].is_rollover_candidate is True
    assert active[0].is_rollover_candidate is False


_KITE = "Symbol,ISIN,Quantity Available,Total Quantity,Average Price\n"


# --- Phase 1.5: refuse lending more than the shares in demat ---------------

_IMPORT_D = datetime.datetime(2026, 10, 1, 4, 0)  # naive UTC = 09:30 IST on 1 Oct
_D = datetime.date(2026, 10, 1)


def _imported(db: Session, qty: int, at: datetime.datetime = _IMPORT_D) -> Stock:
    stock = Stock(symbol="SBIN", quantity=qty, holdings_imported_at=at)
    db.add(stock)
    db.commit()
    db.refresh(stock)
    return stock


def _lend(db: Session, stock: Stock, qty: int, trade_date: datetime.date = _D):
    return ContractService.create_contract(
        db=db, stock_id=stock.id, expiry_date=datetime.date(2026, 11, 3),
        quantity_lent=qty, lending_fee=1.0, series="XN", trade_date=trade_date,
    )


def test_cannot_lend_more_than_imported(db: Session):
    stock = _imported(db, 500)
    with pytest.raises(ValueError, match="in demat"):
        _lend(db, stock, 600)


def test_second_contract_is_checked_against_what_is_left(db: Session):
    stock = _imported(db, 500)
    _lend(db, stock, 300)
    with pytest.raises(ValueError, match="200"):
        _lend(db, stock, 300)
    _lend(db, stock, 200)  # exactly what is left


def test_closing_a_contract_frees_its_shares(db: Session):
    stock = _imported(db, 500)
    first = _lend(db, stock, 300)
    first.status = "CLOSED"  # Phase 4 adds a close action; set directly until then
    db.commit()
    _lend(db, stock, 300)


def test_reimport_is_not_double_subtracted(db: Session):
    stock = _imported(db, 500)
    _lend(db, stock, 300)
    # Next day's export shows 200: the 300 lent on 1 Oct already left demat.
    stock.quantity = 200
    stock.holdings_imported_at = datetime.datetime(2026, 10, 2, 4, 0)
    db.commit()
    _lend(db, stock, 200, trade_date=datetime.date(2026, 10, 2))
    with pytest.raises(ValueError):
        _lend(db, stock, 1, trade_date=datetime.date(2026, 10, 2))


# --- Relaxed rule: contracts traded before the last import's IST date ------

_MON = datetime.date(2026, 10, 5)
_TUE_IMPORT = datetime.datetime(2026, 10, 6, 4, 0)  # 09:30 IST Tue 6 Oct, naive UTC


def _monday_lent_tuesday_imported(db: Session) -> Stock:
    """500 SBIN imported Fri; all 500 lent Mon; Tue's export leaves SBIN out."""
    PortfolioService.import_zerodha_holdings(
        db, _KITE + "NSE:SBIN,,500,500,800\nNSE:INFY,,10,10,1500\n",
        now=datetime.datetime(2026, 10, 2, 4, 0),
    )
    result = PortfolioService.import_zerodha_holdings(db, _KITE + "NSE:INFY,,10,10,1500\n", now=_TUE_IMPORT)
    assert result["zeroed"] == ["SBIN"]
    return db.query(Stock).filter_by(symbol="SBIN").one()


def test_contract_traded_before_last_import_is_accepted_with_a_warning(db: Session):
    sbin = _monday_lent_tuesday_imported(db)
    contract, warning = ContractService.create_contract_with_warning(
        db=db, stock_id=sbin.id, expiry_date=datetime.date(2026, 11, 3),
        quantity_lent=500, lending_fee=1.0, series="XN", trade_date=_MON,
    )
    assert contract.id is not None
    assert warning == (
        "Traded before your last holdings import (06 Oct). "
        "Check that Total owned matches Zerodha."
    )
    pos = PortfolioService.holding_position(db, sbin)
    assert (pos.in_demat, pos.lent, pos.total_owned) == (0, 500, 500)


def test_same_day_contract_above_in_demat_is_still_refused(db: Session):
    sbin = _monday_lent_tuesday_imported(db)
    with pytest.raises(ValueError, match="in demat"):
        ContractService.create_contract_with_warning(
            db=db, stock_id=sbin.id, expiry_date=datetime.date(2026, 11, 3),
            quantity_lent=1, lending_fee=1.0, trade_date=datetime.date(2026, 10, 6),
        )


def test_backdated_contract_still_needs_positive_quantity_and_a_holding(db: Session):
    sbin = _monday_lent_tuesday_imported(db)
    with pytest.raises(ValueError, match="positive"):
        ContractService.create_contract_with_warning(
            db=db, stock_id=sbin.id, expiry_date=datetime.date(2026, 11, 3),
            quantity_lent=0, lending_fee=1.0, trade_date=_MON,
        )
    with pytest.raises(ValueError, match="not found"):
        ContractService.create_contract_with_warning(
            db=db, stock_id=9999, expiry_date=datetime.date(2026, 11, 3),
            quantity_lent=10, lending_fee=1.0, trade_date=_MON,
        )


def test_strict_path_returns_no_warning(db: Session):
    stock = _imported(db, 500)
    _, warning = ContractService.create_contract_with_warning(
        db=db, stock_id=stock.id, expiry_date=datetime.date(2026, 11, 3),
        quantity_lent=100, lending_fee=1.0, trade_date=_D,
    )
    assert warning is None


def test_new_contract_is_its_own_origin_and_keeps_its_series_code(db: Session):
    stock = _imported(db, 500)
    c = ContractService.create_contract(db=db, stock_id=stock.id, expiry_date=datetime.date(2026, 11, 3),
                                        quantity_lent=100, lending_fee=1.0, series="Nov-2026",
                                        series_code="XN", trade_date=_D)
    assert (c.origin_date, c.series_code, c.rolled_from_id, c.closed_on) == (_D, "XN", None, None)
