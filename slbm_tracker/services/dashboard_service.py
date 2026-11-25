"""Dashboard aggregation service.

Assembles the summary payload backing the frontend Dashboard view: portfolio
totals, contract status counts, income roll-ups, upcoming expiries, recent
triggered alerts, and the best current lending opportunities across the
tracked portfolio.
"""

from __future__ import annotations

import datetime
import logging
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from slbm_tracker.config import get_settings
from slbm_tracker.db.models import (
    Alert,
    LendingContract,
    PnLRecord,
    SLBSnapshot,
    Stock,
    StockPriceHistory,
)
from slbm_tracker.services.contract_service import STATUS_ACTIVE, ContractService, today_ist
from slbm_tracker.services import opportunity_service
from slbm_tracker.services.pnl_service import PnLService
from slbm_tracker.services.portfolio_service import PortfolioService
from slbm_tracker.utils.money import money_to_float, to_decimal

logger = logging.getLogger(__name__)

# The "expiring soon" window is owned by ContractService (config-driven) so the
# dashboard cannot drift from the Contracts page; it is not redefined here.

# Caps for embedded list sections to keep the payload light for the dashboard.
MAX_UPCOMING_EXPIRIES = 10
MAX_BEST_OPPORTUNITIES = 5
MAX_RECENT_ALERTS = 10


class DashboardService:
    """Read-only aggregations powering ``GET /api/dashboard/summary``."""

    @staticmethod
    def _financial_year_bounds(today: datetime.date) -> tuple[datetime.date, datetime.date]:
        """Return the Indian financial year (Apr 1 - Mar 31) enclosing ``today``."""
        if today.month >= 4:
            start_year = today.year
        else:
            start_year = today.year - 1
        return datetime.date(start_year, 4, 1), datetime.date(start_year + 1, 3, 31)

    @classmethod
    def build_summary(cls, db: Session, today: Optional[datetime.date] = None) -> dict:
        """Compute the full dashboard summary payload as a plain dict."""
        # IST for date-consistency with the contract engine (whole app is IST).
        today = today or today_ist()
        fy_start, fy_end = cls._financial_year_bounds(today)

        # --- Portfolio totals ---
        total_stocks = db.query(func.count(Stock.id)).scalar() or 0
        slb_eligible_stocks = (
            db.query(func.count(Stock.id)).filter(Stock.is_slb_eligible.is_(True)).scalar() or 0
        )
        total_quantity = db.query(func.coalesce(func.sum(Stock.quantity), 0)).scalar() or 0
        # Shares held = total owned (in demat + lent): Zerodha's export, and so
        # total_quantity, leaves lent shares out (house rule 5).
        positions = PortfolioService.holding_positions(db, db.query(Stock).all()).values()
        in_demat = sum(p.in_demat for p in positions)
        lent = sum(p.lent for p in positions)
        portfolio_cost_value = (
            db.query(func.coalesce(func.sum(Stock.quantity * Stock.avg_buy_price), 0.0)).scalar()
            or 0.0
        )

        # --- Contract status counts (DERIVED, so they cannot drift from the
        # Contracts page). ACTIVE / EXPIRING_SOON / EXPIRED come from the single
        # ContractService derivation rather than the stored status column, which
        # keeps a past-expiry-unreconciled contract from being miscounted as
        # active. ---
        contract_views = ContractService.list_contracts(db=db, today=today)
        active_contracts = sum(1 for v in contract_views if v.effective_status == "ACTIVE")
        expiring_soon = sum(1 for v in contract_views if v.effective_status == "EXPIRING_SOON")
        expired_contracts = sum(1 for v in contract_views if v.effective_status == "EXPIRED")

        # --- Income roll-ups ---
        total_gross_fee = (
            db.query(func.coalesce(func.sum(PnLRecord.gross_fee), 0.0)).scalar() or 0.0
        )
        total_net_income = (
            db.query(func.coalesce(func.sum(PnLRecord.net_income), 0.0)).scalar() or 0.0
        )
        current_fy_net_income = (
            db.query(func.coalesce(func.sum(PnLRecord.net_income), 0.0))
            .join(LendingContract, PnLRecord.contract_id == LendingContract.id)
            .filter(
                LendingContract.expiry_date >= fy_start,
                LendingContract.expiry_date <= fy_end,
            )
            .scalar()
            or 0.0
        )

        # --- Upcoming expiries (active contracts, nearest first) ---
        upcoming_rows = (
            db.query(LendingContract, Stock.symbol)
            .join(Stock, LendingContract.stock_id == Stock.id)
            .filter(
                LendingContract.status == "ACTIVE",
                LendingContract.expiry_date >= today,
            )
            .order_by(LendingContract.expiry_date.asc())
            .limit(MAX_UPCOMING_EXPIRIES)
            .all()
        )
        upcoming_expiries = [
            {
                "contract_id": contract.id,
                "symbol": symbol,
                "series": contract.series,
                "expiry_date": contract.expiry_date,
                "quantity_lent": contract.quantity_lent,
                "lending_fee": contract.lending_fee,
                "days_remaining": (contract.expiry_date - today).days,
            }
            for contract, symbol in upcoming_rows
        ]

        # --- Best opportunities: latest snapshot per portfolio symbol, top yield ---
        best_opportunities = cls._best_opportunities(db, today)
        lent_out = cls._lent_out(db)

        # --- Recent triggered alerts ---
        alert_rows = (
            db.query(Alert)
            .filter(Alert.is_triggered.is_(True))
            .order_by(Alert.last_triggered_at.desc().nullslast())
            .limit(MAX_RECENT_ALERTS)
            .all()
        )
        recent_alerts = [
            {
                "id": alert.id,
                "stock_id": alert.stock_id,
                "alert_type": alert.alert_type,
                "threshold": alert.threshold,
                "message": alert.trigger_message,
                "triggered_at": alert.last_triggered_at,
            }
            for alert in alert_rows
        ]

        return {
            "generated_at": datetime.datetime.utcnow(),
            "portfolio": {
                "total_stocks": int(total_stocks),
                "slb_eligible_stocks": int(slb_eligible_stocks),
                "total_quantity": int(total_quantity),
                "in_demat": int(in_demat),
                "lent": int(lent),
                "total_owned": int(in_demat + lent),
                "portfolio_cost_value": money_to_float(portfolio_cost_value),
            },
            "contracts": {
                "active": int(active_contracts),
                "expiring_soon": int(expiring_soon),
                "expired": int(expired_contracts),
            },
            "income": {
                "total_gross_fee": money_to_float(total_gross_fee),
                "total_net_income": money_to_float(total_net_income),
                "current_fy_net_income": money_to_float(current_fy_net_income),
                "financial_year": f"{fy_start.year}-{fy_end.year}",
            },
            "lent_out": lent_out,
            "upcoming_expiries": upcoming_expiries,
            "best_opportunities": best_opportunities,
            "recent_alerts": recent_alerts,
        }

    @staticmethod
    def _last_price(db: Session, symbol: str) -> Optional[float]:
        """Latest known share price (see opportunity_service.last_price)."""
        return opportunity_service.last_price(db, symbol)

    @classmethod
    def _lent_out(cls, db: Session) -> dict:
        """Shares out on loan under ACTIVE contracts, valued at the last known price.

        Fee due is each ACTIVE contract's quantity × fee, gross and net of
        processing, GST and the flat DP charge (PnLService, Decimal). A stock
        with no known price is listed in ``unpriced_symbols`` rather than valued
        at a guess.
        """
        lent_ids = db.query(LendingContract.stock_id).filter(LendingContract.status == STATUS_ACTIVE)
        stocks = db.query(Stock).filter(Stock.id.in_(lent_ids)).order_by(Stock.symbol.asc()).all()
        positions = PortfolioService.holding_positions(db, stocks)

        shares = 0
        value = to_decimal(0)
        gross = to_decimal(0)
        net = to_decimal(0)
        unpriced: list[str] = []
        for stock in stocks:
            pos = positions[stock.id]
            shares += pos.lent
            price = cls._last_price(db, stock.symbol)
            if price is None:
                unpriced.append(stock.symbol)
            else:
                value += to_decimal(pos.lent) * to_decimal(price)
            for c in pos.active_contracts:
                b = PnLService.compute_breakdown(c.quantity_lent, c.lending_fee)
                gross += to_decimal(b.gross_fee)
                net += to_decimal(b.net_income)

        return {
            "stocks": len(stocks),
            "shares": shares,
            "value_at_last_price": money_to_float(value),
            "unpriced_symbols": unpriced,
            "fee_income_due_gross": money_to_float(gross),
            "fee_income_due_net": money_to_float(net),
        }

    @classmethod
    def _best_opportunities(
        cls, db: Session, today: datetime.date, now: Optional[datetime.datetime] = None
    ) -> list[dict]:
        """Top actionable lending opportunities (Phase 3.5), from opportunity_service.

        Priced at the live Series B bid on the fillable quantity, net of
        Zerodha's charges; ranked by net annualised yield. ``annualized_yield``
        is the NET yield and ``ltp`` the bid the ticket would lend at.
        """
        board = opportunity_service.build(db, today=today, now=now)
        return [
            {
                "symbol": o["symbol"],
                "series": o["series_code"],
                "expiry_date": o["settlement_date"],
                "ltp": o["best_bid"],
                "best_bid": o["best_bid"],
                "best_ask": o["best_ask"] or 0.0,
                "annualized_yield": money_to_float(o["net_yield_pa"]),
                "open_positions": 0,
                "timestamp": o["quote_at"],
                "fillable": o["fillable"],
                "net_fee": money_to_float(o["net_fee"]),
                "days_to_settlement": o["days_to_settlement"],
                "ticket": o["ticket"],
            }
            for o in opportunity_service.actionable_options(board)[:MAX_BEST_OPPORTUNITIES]
        ]
