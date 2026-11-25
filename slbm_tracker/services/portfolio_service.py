"""Portfolio service for managing stock holdings and CSV imports."""

from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from slbm_tracker.db.models import LendingContract, Stock
from slbm_tracker.services.contract_service import IST, STATUS_ACTIVE
from slbm_tracker.utils.zerodha_parser import ParsedHolding, ZerodhaHoldingsParser

logger = logging.getLogger(__name__)

SYMBOL_VALIDATION_REGEX = re.compile(r"^[A-Z0-9&\-_.]+$")
# A holding NSE reported as not on the SLB board is re-checked after this long.
SLB_RECHECK_INTERVAL = datetime.timedelta(days=7)


@dataclass(frozen=True)
class HoldingPosition:
    """Where a stock's shares are (house rule 5).

    Zerodha's holdings export leaves lent shares out from the T+1 forward-leg
    settlement, so an export taken on the trade day still includes them:

    * lent        = sum of ACTIVE contracts
    * in_demat    = imported quantity − ACTIVE contracts whose trade_date is on
                    or after the IST date of ``holdings_imported_at`` (all
                    ACTIVE contracts when the stock was never imported)
    * total_owned = in_demat + lent
    * lendable    = in_demat − pledged (never below 0; Phase 3.2)

    Phase 4.2: a contract the last import already left out (traded before the
    import date) that stops being lent AFTER that date (closed, recalled or
    rolled; ``closed_on`` later than the import's IST date) brings its shares
    back into in_demat until the next import shows them. A roll's new contract
    is ACTIVE and subtracted as usual, so a roll nets to zero.
    """

    stock_id: int
    imported_quantity: int
    in_demat: int
    lent: int
    total_owned: int
    pledged: int = 0
    lendable: int = 0
    active_contracts: tuple[LendingContract, ...] = field(default_factory=tuple)


def import_date_ist(imported_at: Optional[datetime.datetime]) -> Optional[datetime.date]:
    """IST calendar date of a naive-UTC import timestamp (None if never imported)."""
    if imported_at is None:
        return None
    aware = imported_at if imported_at.tzinfo else imported_at.replace(tzinfo=datetime.timezone.utc)
    return aware.astimezone(IST).date()


def _position(
    stock: Stock,
    active: Iterable[LendingContract],
    ended: Iterable[LendingContract] = (),
) -> HoldingPosition:
    active = tuple(sorted(active, key=lambda c: (c.trade_date, c.id)))
    cutoff = import_date_ist(stock.holdings_imported_at)
    imported = int(stock.quantity or 0)
    lent = sum(int(c.quantity_lent) for c in active)
    still_in_import = sum(
        int(c.quantity_lent) for c in active if cutoff is None or c.trade_date >= cutoff
    )
    # Shares the import left out (lent before it) that came back after it.
    returned_since_import = (
        0
        if cutoff is None
        else sum(
            int(c.quantity_lent)
            for c in ended
            if c.closed_on is not None and c.trade_date < cutoff and c.closed_on > cutoff
        )
    )
    still_in_import -= returned_since_import
    in_demat = imported - still_in_import
    return HoldingPosition(
        stock_id=stock.id,
        imported_quantity=imported,
        in_demat=in_demat,
        lent=lent,
        total_owned=in_demat + lent,
        pledged=int(stock.pledged_quantity or 0),
        lendable=max(in_demat - int(stock.pledged_quantity or 0), 0),
        active_contracts=active,
    )


class PortfolioService:
    """Business logic for portfolio operations."""

    @staticmethod
    def validate_symbol(symbol: str) -> str:
        """Validate and normalize a stock ticker symbol."""
        if not symbol or not symbol.strip():
            raise ValueError("Stock symbol cannot be empty.")
        cleaned = symbol.strip().upper()
        if not SYMBOL_VALIDATION_REGEX.match(cleaned):
            raise ValueError(f"Invalid symbol format: '{symbol}'. Must be alphanumeric.")
        if len(cleaned) > 25:
            raise ValueError(f"Symbol '{symbol}' is too long (max 25 characters).")
        return cleaned

    @classmethod
    def add_or_update_stock(
        cls,
        db: Session,
        symbol: str,
        quantity: int,
        avg_buy_price: float = 0.0,
        name: Optional[str] = None,
        isin: Optional[str] = None,
        is_slb_eligible: bool = True,
        notes: Optional[str] = None,
    ) -> Stock:
        """Add a new stock or update an existing one."""
        clean_sym = cls.validate_symbol(symbol)

        if quantity < 0:
            raise ValueError(f"Quantity must be non-negative, got {quantity}.")
        if avg_buy_price < 0:
            raise ValueError(f"Average buy price must be non-negative, got {avg_buy_price}.")

        stock = db.query(Stock).filter(Stock.symbol == clean_sym).first()

        if stock:
            logger.info("Updating existing stock %s in portfolio", clean_sym)
            stock.quantity = quantity
            stock.avg_buy_price = avg_buy_price
            if name is not None:
                stock.name = name
            if isin is not None:
                stock.isin = isin
            stock.is_slb_eligible = is_slb_eligible
            if notes is not None:
                stock.notes = notes
            stock.updated_at = datetime.datetime.utcnow()
        else:
            logger.info("Adding new stock %s to portfolio", clean_sym)
            stock = Stock(
                symbol=clean_sym,
                name=name,
                isin=isin,
                quantity=quantity,
                avg_buy_price=avg_buy_price,
                is_slb_eligible=is_slb_eligible,
                notes=notes,
            )
            db.add(stock)

        db.commit()
        db.refresh(stock)
        return stock

    @classmethod
    def get_stock(cls, db: Session, stock_id: int) -> Optional[Stock]:
        """Fetch a stock by primary key ID."""
        return db.query(Stock).filter(Stock.id == stock_id).first()

    @classmethod
    def get_stock_by_symbol(cls, db: Session, symbol: str) -> Optional[Stock]:
        """Fetch a stock by symbol."""
        clean_sym = cls.validate_symbol(symbol)
        return db.query(Stock).filter(Stock.symbol == clean_sym).first()

    @classmethod
    def list_stocks(
        cls,
        db: Session,
        skip: int = 0,
        limit: int = 100,
        is_slb_eligible: Optional[bool] = None,
    ) -> list[Stock]:
        """Retrieve list of stocks with optional eligibility filter."""
        query = db.query(Stock)
        if is_slb_eligible is not None:
            query = query.filter(Stock.is_slb_eligible == is_slb_eligible)
        return query.order_by(Stock.symbol.asc()).offset(skip).limit(limit).all()

    @classmethod
    def holding_positions(
        cls, db: Session, stocks: Iterable[Stock]
    ) -> dict[int, HoldingPosition]:
        """In demat / lent / total owned for each stock, keyed by stock id. One query."""
        stocks = list(stocks)
        by_stock: dict[int, list[LendingContract]] = {s.id: [] for s in stocks}
        ended: dict[int, list[LendingContract]] = {s.id: [] for s in stocks}
        if by_stock:
            rows = (
                db.query(LendingContract)
                .filter(
                    LendingContract.stock_id.in_(list(by_stock)),
                    or_(LendingContract.status == STATUS_ACTIVE, LendingContract.closed_on.isnot(None)),
                )
                .all()
            )
            for c in rows:
                (by_stock if c.status == STATUS_ACTIVE else ended)[c.stock_id].append(c)
        return {s.id: _position(s, by_stock[s.id], ended[s.id]) for s in stocks}

    @classmethod
    def holding_position(cls, db: Session, stock: Stock) -> HoldingPosition:
        """In demat / lent / total owned for one stock (see :class:`HoldingPosition`)."""
        return cls.holding_positions(db, [stock])[stock.id]

    @classmethod
    def polled_symbols(
        cls, db: Session, now: Optional[datetime.datetime] = None, *, include_ineligible: bool = False
    ) -> list[str]:
        """Symbols the scheduler refreshes from NSE: held (quantity > 0) or lent.

        A stock under an ACTIVE contract stays polled even at quantity 0,
        because Zerodha's holdings export leaves lent shares out. A holding NSE
        reported as not on the SLB board (Phase 2.12) is skipped until its last
        check is a week old. The tracked symbol list is for browsing only and is
        never polled. Bulk month sweeps include ineligible holdings as they cost no
        additional upstream requests and can correct an old eligibility result.
        Sorted for a stable rotation order. ``now`` is naive UTC.
        """
        now = now or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        lent_ids = db.query(LendingContract.stock_id).filter(
            LendingContract.status == STATUS_ACTIVE
        )
        due_for_recheck = or_(
            Stock.slb_checked_at.is_(None),
            Stock.slb_checked_at <= now - SLB_RECHECK_INTERVAL,
        )
        rows = (
            db.query(Stock.symbol)
            .filter(
                or_(
                    Stock.id.in_(lent_ids),
                    and_(Stock.quantity > 0, or_(include_ineligible, Stock.is_slb_eligible.is_(True), due_for_recheck)),
                )
            )
            .order_by(Stock.symbol.asc())
            .all()
        )
        return [r[0] for r in rows]

    @classmethod
    def record_slb_check(
        cls, db: Session, symbol: str, on_slb: bool, checked_at: datetime.datetime
    ) -> None:
        """Set is_slb_eligible from whether NSE returned any series (naive-UTC time)."""
        stock = db.query(Stock).filter(Stock.symbol == symbol).first()
        if stock is None:
            return
        if stock.is_slb_eligible != on_slb:
            logger.info("%s is %s the SLB board.", symbol, "on" if on_slb else "not on")
        stock.is_slb_eligible = on_slb
        stock.slb_checked_at = checked_at
        db.commit()

    @classmethod
    def delete_stock(cls, db: Session, stock_id: int) -> bool:
        """Remove a stock and cascade-delete related records."""
        stock = db.query(Stock).filter(Stock.id == stock_id).first()
        if not stock:
            return False
        logger.info("Deleting stock ID %s (%s) from portfolio", stock_id, stock.symbol)
        db.delete(stock)
        db.commit()
        return True

    @classmethod
    def import_zerodha_holdings(
        cls,
        db: Session,
        csv_content: str,
        now: Optional[datetime.datetime] = None,
    ) -> dict:
        """
        Import Zerodha holdings CSV into the portfolio.

        The file is the full list of holdings in demat: every existing stock
        missing from it is set to quantity 0 (never deleted, so its contract
        history stays) and listed in ``zeroed``. Every stock the import sets,
        including zeroed ones, records ``holdings_imported_at`` (naive UTC)
        for the in-demat arithmetic in :class:`HoldingPosition`.

        Zeroing is skipped, with the reason in ``zeroing_skipped``, when the
        file yields no holdings or any row could not be read: a stock on an
        unreadable row would otherwise be zeroed by mistake.

        Returns summary statistics and any parsing warnings.
        """
        imported_at = now or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        parsed_items, parse_errors = ZerodhaHoldingsParser.parse_csv(csv_content)

        added_count = 0
        updated_count = 0
        imported_stocks: list[Stock] = []
        errors = list(parse_errors)

        # Collapse duplicate symbols within a single CSV (last occurrence wins).
        # Stock.symbol is UNIQUE. Two rows for the same symbol would otherwise
        # create two pending INSERTs that only collide at the single commit
        # below; because ``autoflush=False`` hides each pending insert from the
        # mid-loop existence query, the collision surfaces as an IntegrityError
        # on commit and loses the ENTIRE batch (MED M2). Deduping up front keeps
        # the import atomic and its result consistent (added + updated == parsed).
        deduped: dict[str, ParsedHolding] = {}
        for item in parsed_items:
            if item.symbol in deduped:
                errors.append(
                    f"Duplicate symbol '{item.symbol}' in CSV; kept the last occurrence."
                )
            deduped[item.symbol] = item
        items = list(deduped.values())

        for item in items:
            try:
                existing = db.query(Stock).filter(Stock.symbol == item.symbol).first()
                if existing:
                    existing.quantity = item.quantity
                    existing.pledged_quantity = item.pledged_quantity
                    existing.holdings_imported_at = imported_at
                    existing.avg_buy_price = item.avg_buy_price
                    if item.isin:
                        existing.isin = item.isin
                    if item.name:
                        existing.name = item.name
                    existing.updated_at = datetime.datetime.utcnow()
                    updated_count += 1
                    imported_stocks.append(existing)
                else:
                    new_stock = Stock(
                        symbol=item.symbol,
                        quantity=item.quantity,
                        avg_buy_price=item.avg_buy_price,
                        isin=item.isin,
                        name=item.name,
                        notes=item.notes,
                        holdings_imported_at=imported_at,
                        pledged_quantity=item.pledged_quantity,
                    )
                    db.add(new_stock)
                    added_count += 1
                    imported_stocks.append(new_stock)
            except Exception as ex:
                logger.error("Error importing holding %s: %s", item.symbol, ex)
                errors.append(f"Failed to import {item.symbol}: {str(ex)}")

        zeroed: list[str] = []
        zeroing_skipped: Optional[str] = None
        if not items:
            zeroing_skipped = "The file has no holdings, so no stock was set to 0."
        elif parse_errors:
            zeroing_skipped = (
                f"{len(parse_errors)} row(s) could not be read, so stocks missing from the "
                "file were left unchanged."
            )
        else:
            in_file = {item.symbol for item in items}
            for stock in (
                db.query(Stock)
                .filter(Stock.symbol.notin_(in_file), Stock.quantity > 0)
                .order_by(Stock.symbol.asc())
                .all()
            ):
                stock.quantity = 0
                stock.pledged_quantity = 0
                stock.holdings_imported_at = imported_at
                stock.updated_at = imported_at
                zeroed.append(stock.symbol)
            if zeroed:
                logger.info("Zerodha import set %s stock(s) to 0: %s", len(zeroed), ", ".join(zeroed))

        try:
            db.commit()
        except Exception:
            # Never leave the session in a half-applied, poisoned state.
            db.rollback()
            raise
        for s in imported_stocks:
            db.refresh(s)

        return {
            "total_parsed": len(items),
            "added": added_count,
            "updated": updated_count,
            "errors": errors,
            "stocks": imported_stocks,
            "zeroed": zeroed,
            "zeroing_skipped": zeroing_skipped,
        }
