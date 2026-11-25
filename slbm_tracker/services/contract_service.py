"""Active SLB lending-contract domain service.

Owns contract creation, retrieval, and the *derived* lifecycle state. A key
design rule (Phase 3 spec §2): the time-based status of a contract must never
drift from its dates.

Status model
------------
The stored ``LendingContract.status`` column holds only *business lifecycle*
states that a human/action sets and that are NOT derivable from dates:

* ``ACTIVE``      — open contract, still within or awaiting its term
* ``RECALLED``    — lender recalled early
* ``CLOSED``      — settled/closed early
* ``ROLLED_OVER`` — superseded by a rollover

The *time-based* states are always **derived** from the expiry date vs. today
(in IST), never stored, so they cannot drift:

* ``EXPIRING_SOON`` — ACTIVE and within the expiring-soon window
* ``EXPIRED``       — ACTIVE and past expiry

Only one persisted transition exists: an ``ACTIVE`` contract whose expiry has
passed can be reconciled to the stored ``EXPIRED`` state (idempotent). Terminal
business states (RECALLED/CLOSED/ROLLED_OVER) are returned as-is.

Dates are date-only and evaluated against "today in IST" because this is an
NSE/India application; using naive UTC could flip a boundary by a day for users
in IST during the UTC evening.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from slbm_tracker.db.models import LendingContract, Stock
from slbm_tracker.services import business_config
from slbm_tracker.utils.money import quantize_money

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# Derived (never-stored) time-based statuses.
STATUS_ACTIVE = "ACTIVE"
STATUS_EXPIRING_SOON = "EXPIRING_SOON"
STATUS_EXPIRED = "EXPIRED"
STATUS_CLOSED = "CLOSED"
STATUS_RECALLED = "RECALLED"
STATUS_ROLLED_OVER = "ROLLED_OVER"
# SEBI: a lending chain may run at most this many days from its ORIGINAL date.
MAX_CHAIN_DAYS = 365


class ContractStateError(ValueError):
    """The action does not fit the contract's current status (API: 409)."""
# Terminal business states (stored, returned verbatim).
TERMINAL_STATUSES = frozenset({"RECALLED", "CLOSED", "ROLLED_OVER"})


def today_ist() -> datetime.date:
    """Return the current calendar date in IST (Asia/Kolkata)."""
    return datetime.datetime.now(IST).date()


@dataclass(frozen=True)
class ContractView:
    """A contract enriched with derived, date-consistent fields for the API."""

    id: int
    stock_id: int
    symbol: str
    series: Optional[str]
    trade_date: datetime.date
    expiry_date: datetime.date
    reverse_leg_date: Optional[datetime.date]
    quantity_lent: int
    lending_fee: float  # per share
    stored_status: str
    effective_status: str
    days_remaining: int  # signed: negative once past expiry
    tenure_days: int
    lending_fee_income: float  # quantity_lent * lending_fee, rounded (INR)
    is_rollover_candidate: bool
    notes: Optional[str]
    # Phase 4: the roll chain and SEBI's 365-day cap.
    origin_date: Optional[datetime.date] = None
    rolled_from_id: Optional[int] = None
    series_code: Optional[str] = None
    closed_on: Optional[datetime.date] = None
    chain_days: Optional[int] = None  # settlement - origin_date (of 365)


class ContractService:
    """Create/read lending contracts and derive their lifecycle state."""

    @staticmethod
    def expiring_soon_days() -> int:
        """Window (in days) within which an active contract is 'expiring soon'."""
        return int(business_config.effective("expiring_soon_days"))

    @classmethod
    def derive_status(
        cls,
        stored_status: str,
        expiry_date: datetime.date,
        today: Optional[datetime.date] = None,
        soon_days: Optional[int] = None,
    ) -> str:
        """Compute the effective status from the stored status + expiry vs today.

        Boundary rules (date-only, IST):
        * days_remaining > soon_days           → ACTIVE
        * 0 <= days_remaining <= soon_days      → EXPIRING_SOON
        * days_remaining < 0 (today > expiry)   → EXPIRED
        On the expiry date itself (days_remaining == 0) the contract is still
        EXPIRING_SOON, not EXPIRED — the SLB reverse leg settles through the
        expiry day. It becomes EXPIRED only the day AFTER expiry.
        Terminal business states are returned unchanged.
        """
        if stored_status in TERMINAL_STATUSES:
            return stored_status
        today = today or today_ist()
        soon_days = cls.expiring_soon_days() if soon_days is None else soon_days
        days_remaining = (expiry_date - today).days
        if days_remaining < 0:
            return STATUS_EXPIRED
        if days_remaining <= soon_days:
            return STATUS_EXPIRING_SOON
        return STATUS_ACTIVE

    @classmethod
    def to_view(
        cls, contract: LendingContract, symbol: str, today: Optional[datetime.date] = None
    ) -> ContractView:
        """Build a fully-derived :class:`ContractView` for one contract."""
        today = today or today_ist()
        soon_days = cls.expiring_soon_days()
        effective = cls.derive_status(contract.status, contract.expiry_date, today, soon_days)
        days_remaining = (contract.expiry_date - today).days
        tenure_days = (contract.expiry_date - contract.trade_date).days
        income = quantize_money(
            cls._income(contract.quantity_lent, contract.lending_fee)
        )
        # A rollover candidate is an open (non-terminal) contract at/inside the
        # expiring-soon window (incl. already past expiry but not yet closed).
        is_candidate = (
            contract.status not in TERMINAL_STATUSES and days_remaining <= soon_days
        )
        return ContractView(
            id=contract.id,
            stock_id=contract.stock_id,
            symbol=symbol,
            series=contract.series,
            trade_date=contract.trade_date,
            expiry_date=contract.expiry_date,
            reverse_leg_date=contract.reverse_leg_date,
            quantity_lent=contract.quantity_lent,
            lending_fee=float(contract.lending_fee),
            stored_status=contract.status,
            effective_status=effective,
            days_remaining=days_remaining,
            tenure_days=tenure_days,
            lending_fee_income=float(income),
            is_rollover_candidate=is_candidate,
            notes=contract.notes,
            origin_date=contract.origin_date,
            rolled_from_id=contract.rolled_from_id,
            series_code=contract.series_code,
            closed_on=contract.closed_on,
            chain_days=(
                ((contract.reverse_leg_date or contract.expiry_date) - contract.origin_date).days
                if contract.origin_date
                else None
            ),
        )

    @staticmethod
    def _income(quantity_lent: int, lending_fee_per_share) -> "object":
        """Gross lending fee income = quantity_lent × fee-per-share (Decimal)."""
        from slbm_tracker.utils.money import to_decimal

        return to_decimal(quantity_lent) * to_decimal(lending_fee_per_share)

    @classmethod
    def create_contract(cls, db: Session, **kwargs) -> LendingContract:
        """Create a lending contract; see :meth:`create_contract_with_warning`."""
        contract, _warning = cls.create_contract_with_warning(db, **kwargs)
        return contract

    @classmethod
    def create_contract_with_warning(
        cls,
        db: Session,
        *,
        stock_id: int,
        expiry_date: datetime.date,
        quantity_lent: int,
        lending_fee: float,
        series: Optional[str] = None,
        trade_date: Optional[datetime.date] = None,
        reverse_leg_date: Optional[datetime.date] = None,
        notes: Optional[str] = None,
        series_code: Optional[str] = None,
    ) -> tuple[LendingContract, Optional[str]]:
        """Create a lending contract against an existing portfolio stock.

        Validates the referenced stock exists and inputs are sane. A contract
        represents shares actually lent out, so it must reference a real
        holding — we do not fabricate a stock here.

        Quantity (house rule 5): a contract traded on or after the IST date of
        the last holdings import must fit in the shares still in demat. One
        traded before that date is already reflected in the import (its shares
        were out of the export), so it cannot be checked against it: it is
        accepted and the returned warning asks the user to compare Total owned
        with Zerodha. Returns ``(contract, warning or None)``.
        """
        stock = db.query(Stock).filter(Stock.id == stock_id).first()
        if stock is None:
            raise ValueError(
                f"Stock id {stock_id} not found. A lending contract must reference "
                "an existing portfolio holding."
            )
        if quantity_lent <= 0:
            raise ValueError("quantity_lent must be a positive number of shares.")
        if lending_fee < 0:
            raise ValueError("lending_fee (per share) must be non-negative.")

        trade_date = trade_date or today_ist()
        if expiry_date < trade_date:
            raise ValueError("expiry_date cannot be before trade_date.")

        # Only shares still in demat can be lent (house rule 5). Imported lazily:
        # portfolio_service imports this module.
        from slbm_tracker.services.portfolio_service import PortfolioService, import_date_ist

        warning: Optional[str] = None
        imported_on = import_date_ist(stock.holdings_imported_at)
        position = PortfolioService.holding_position(db, stock)
        if imported_on is not None and trade_date < imported_on:
            warning = (
                f"Traded before your last holdings import ({imported_on.strftime('%d %b')}). "
                "Check that Total owned matches Zerodha."
            )
        elif quantity_lent > position.lendable:
            pledged = f", {position.pledged} pledged" if position.pledged else ""
            raise ValueError(
                f"Only {position.lendable} shares of {stock.symbol} can be lent: "
                f"{max(position.in_demat, 0)} in demat "
                f"(imported {position.imported_quantity}, "
                f"{position.imported_quantity - position.in_demat} lent since that import{pledged}); "
                f"cannot lend {quantity_lent}."
            )

        contract = LendingContract(
            stock_id=stock_id,
            series=series,
            expiry_date=expiry_date,
            quantity_lent=quantity_lent,
            # Per-share fee is a RATE, not a money total — store as given and do
            # not quantize to paise here (that would silently lose rate
            # precision). Only money totals (income, P&L) are quantized.
            lending_fee=float(lending_fee),
            status=STATUS_ACTIVE,
            trade_date=trade_date,
            reverse_leg_date=reverse_leg_date,
            notes=notes,
            series_code=(series_code or "").strip().upper() or None,
            # A fresh contract starts its own chain; rolls carry this forward.
            origin_date=trade_date,
        )
        db.add(contract)
        db.commit()
        db.refresh(contract)
        return contract, warning

    @classmethod
    def list_contracts(
        cls,
        db: Session,
        *,
        effective_status: Optional[str] = None,
        today: Optional[datetime.date] = None,
    ) -> list[ContractView]:
        """List contracts as derived views, optionally filtered by effective status."""
        today = today or today_ist()
        rows = (
            db.query(LendingContract, Stock.symbol)
            .join(Stock, LendingContract.stock_id == Stock.id)
            .order_by(LendingContract.expiry_date.asc())
            .all()
        )
        views = [cls.to_view(c, symbol, today) for c, symbol in rows]
        if effective_status:
            views = [v for v in views if v.effective_status == effective_status]
        return views

    @classmethod
    def get_contract_view(
        cls, db: Session, contract_id: int, today: Optional[datetime.date] = None
    ) -> Optional[ContractView]:
        """Return a single derived contract view, or None if not found."""
        row = (
            db.query(LendingContract, Stock.symbol)
            .join(Stock, LendingContract.stock_id == Stock.id)
            .filter(LendingContract.id == contract_id)
            .first()
        )
        if row is None:
            return None
        contract, symbol = row
        return cls.to_view(contract, symbol, today or today_ist())

    # -- lifecycle actions (Phase 4.2) -----------------------------------------

    @staticmethod
    def settlement_date(contract: LendingContract) -> datetime.date:
        """The reverse-leg date: recorded reverse_leg_date, else expiry_date."""
        return contract.reverse_leg_date or contract.expiry_date

    @classmethod
    def _get(cls, db: Session, contract_id: int) -> LendingContract:
        contract = db.query(LendingContract).filter(LendingContract.id == contract_id).first()
        if contract is None:
            raise LookupError(f"Contract {contract_id} not found.")
        return contract

    @classmethod
    def close_contract(
        cls, db: Session, contract_id: int, today: Optional[datetime.date] = None
    ) -> LendingContract:
        """Close an open contract and book its P&L once.

        P&L goes through PnLService.record_for_contract, an upsert, so it is
        never booked twice. Closing a CLOSED contract changes nothing; a
        RECALLED or ROLLED_OVER one cannot be closed. ``closed_on`` (IST date)
        is when the shares stopped being lent, which the in-demat figure uses.
        """
        from slbm_tracker.services.pnl_service import PnLService

        contract = cls._get(db, contract_id)
        if contract.status == STATUS_CLOSED:
            return contract
        if contract.status in TERMINAL_STATUSES:
            raise ContractStateError(f"Contract {contract_id} is {contract.status}; it cannot be closed.")
        contract.status = STATUS_CLOSED
        contract.closed_on = today or today_ist()
        db.commit()
        PnLService.record_for_contract(db, contract.id)
        db.refresh(contract)
        return contract

    @classmethod
    def recall_contract(
        cls, db: Session, contract_id: int, today: Optional[datetime.date] = None
    ) -> LendingContract:
        """Mark an open contract RECALLED (shares called back early). No P&L is
        booked: the fee actually earned on a recall is not known here, so record
        it with PATCH if needed. Recalling twice changes nothing."""
        contract = cls._get(db, contract_id)
        if contract.status == STATUS_RECALLED:
            return contract
        if contract.status in TERMINAL_STATUSES:
            raise ContractStateError(f"Contract {contract_id} is {contract.status}; it cannot be recalled.")
        contract.status = STATUS_RECALLED
        contract.closed_on = today or today_ist()
        db.commit()
        db.refresh(contract)
        return contract

    @classmethod
    def rollover_contract(
        cls,
        db: Session,
        contract_id: int,
        *,
        new_expiry_date: datetime.date,
        new_lending_fee: float,
        new_series: Optional[str] = None,
        new_series_code: Optional[str] = None,
        new_reverse_leg_date: Optional[datetime.date] = None,
        quantity_lent: Optional[int] = None,
        trade_date: Optional[datetime.date] = None,
        notes: Optional[str] = None,
        today: Optional[datetime.date] = None,
    ) -> LendingContract:
        """Roll an open contract into a later series.

        The old contract becomes ROLLED_OVER (its fee booked once) and a new
        ACTIVE one opens with the same shares, carrying the ORIGINAL
        ``origin_date`` and pointing back via ``rolled_from_id``. Refused when the
        new settlement would be more than 365 days after ``origin_date`` (SEBI
        cap). The same shares are re-lent, so there is no in-demat check; the
        quantity can only stay or shrink.
        """
        from slbm_tracker.services.pnl_service import PnLService

        today = today or today_ist()
        old = cls._get(db, contract_id)
        if old.status in TERMINAL_STATUSES:
            raise ContractStateError(f"Contract {contract_id} is {old.status}; only an open contract can be rolled.")
        qty = old.quantity_lent if quantity_lent is None else int(quantity_lent)
        if qty <= 0 or qty > old.quantity_lent:
            raise ValueError(
                f"A roll re-lends the same shares: quantity must be 1 to {old.quantity_lent}, got {qty}."
            )
        if new_lending_fee < 0:
            raise ValueError("new_lending_fee (per share) must be non-negative.")
        origin = old.origin_date or old.trade_date
        new_settlement = new_reverse_leg_date or new_expiry_date
        if new_settlement <= cls.settlement_date(old):
            raise ValueError("The new series must settle after the current one.")
        span = (new_settlement - origin).days
        if span > MAX_CHAIN_DAYS:
            raise ValueError(
                f"Roll refused: the new settlement {new_settlement.isoformat()} is {span} days after the "
                f"original contract date {origin.isoformat()}; SEBI caps a lending chain at "
                f"{MAX_CHAIN_DAYS} days."
            )
        trade = trade_date or today

        old.status = STATUS_ROLLED_OVER
        old.closed_on = today
        new = LendingContract(
            stock_id=old.stock_id,
            series=new_series,
            series_code=(new_series_code or "").strip().upper() or None,
            expiry_date=new_expiry_date,
            reverse_leg_date=new_reverse_leg_date,
            quantity_lent=qty,
            lending_fee=float(new_lending_fee),
            status=STATUS_ACTIVE,
            trade_date=trade,
            origin_date=origin,
            rolled_from_id=old.id,
            notes=notes,
        )
        db.add(new)
        db.commit()
        PnLService.record_for_contract(db, old.id)
        db.refresh(new)
        db.refresh(old)
        return new

    EDITABLE_FIELDS = frozenset(
        {"notes", "lending_fee", "series", "series_code", "reverse_leg_date", "expiry_date", "trade_date"}
    )

    @classmethod
    def update_contract(cls, db: Session, contract_id: int, **changes) -> LendingContract:
        """Correct a contract's details (PATCH). Status changes go through the
        actions, never here. A booked P&L (closed or rolled) is re-booked by the
        same upsert, so a fee correction never leaves a stale or second record."""
        from slbm_tracker.services.pnl_service import PnLService

        unknown = set(changes) - cls.EDITABLE_FIELDS
        if unknown:
            raise ValueError(f"Cannot edit {sorted(unknown)}.")
        contract = cls._get(db, contract_id)
        if "lending_fee" in changes and changes["lending_fee"] is not None and changes["lending_fee"] < 0:
            raise ValueError("lending_fee (per share) must be non-negative.")
        if changes.get("series_code"):
            changes["series_code"] = changes["series_code"].strip().upper()
        trade = changes.get("trade_date") or contract.trade_date
        expiry = changes.get("expiry_date") or contract.expiry_date
        if expiry < trade:
            raise ValueError("expiry_date cannot be before trade_date.")
        for key, value in changes.items():
            setattr(contract, key, float(value) if key == "lending_fee" else value)
        db.commit()
        if contract.status in (STATUS_CLOSED, STATUS_ROLLED_OVER):
            PnLService.record_for_contract(db, contract.id)
        db.refresh(contract)
        return contract

    @classmethod
    def close_expired_contracts(
        cls, db: Session, today: Optional[datetime.date] = None
    ) -> list[LendingContract]:
        """Auto-close open contracts whose reverse leg has settled (before today).

        Each is closed once, on its settlement date, with P&L booked through the
        idempotent upsert; a contract settling today stays open until tomorrow.
        """
        today = today or today_ist()
        open_rows = (
            db.query(LendingContract)
            .filter(LendingContract.status.in_([STATUS_ACTIVE, STATUS_EXPIRED]))
            .order_by(LendingContract.id.asc())
            .all()
        )
        settled = [c for c in open_rows if cls.settlement_date(c) < today]
        closed = []
        for contract in settled:
            closed.append(cls.close_contract(db, contract.id, today=cls.settlement_date(contract)))
        if closed:
            logger.info("Auto-closed %s settled contract(s).", len(closed))
        return closed

    @classmethod
    def reconcile_expired(cls, db: Session, today: Optional[datetime.date] = None) -> int:
        """Persist ACTIVE→EXPIRED for contracts past expiry. Idempotent.

        This is the ONLY stored status transition the service performs. It never
        touches terminal states and never sets EXPIRING_SOON (that stays
        derived). Returns the number of rows transitioned.

        Intentionally an on-demand maintenance operation: nothing calls it on a
        timer or per-request. Read-path correctness never depends on it because
        ACTIVE/EXPIRING_SOON/EXPIRED are DERIVED at read time (``effective_status``
        / :meth:`to_view`), so an unreconciled past-expiry row is still reported
        (and counted) as EXPIRED. This method only tidies the persisted column for
        callers that query the raw ``status`` directly; invoke it deliberately.
        """
        today = today or today_ist()
        stale = (
            db.query(LendingContract)
            .filter(
                LendingContract.status == STATUS_ACTIVE,
                LendingContract.expiry_date < today,
            )
            .all()
        )
        for contract in stale:
            contract.status = STATUS_EXPIRED
        if stale:
            db.commit()
            logger.info("Reconciled %s contract(s) to EXPIRED.", len(stale))
        return len(stale)
