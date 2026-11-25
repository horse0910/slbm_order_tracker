"""Authoritative P&L / fee-breakdown engine (Phase 3 feature 7).

ALL money math lives here, in Decimal, never binary float (Phase 3 spec §7 and
the money rules). Each cost component is computed and reported separately —
nothing is silently netted or assumed. The tax figure is an explicit,
clearly-labeled ESTIMATE driven by a configurable slab (the user's decision),
never presented as an actual tax liability.

Formula (all intermediates carried UNROUNDED in Decimal; each reported figure is
then independently quantized to paise with ROUND_HALF_UP):

    gross_fee         = quantity_lent × lending_fee_per_share
    processing_charge = gross_fee         × broker_processing_fee_pct%
    gst               = processing_charge × gst_pct%          (GST is ON the processing charge)
    dp_charge         = dp_charge_flat_inr × (1 + gst_pct%)   (flat per scrip, 0 if nothing lent)
    net_income        = gross_fee − processing_charge − gst − dp_charge
    estimated_tax     = net_income        × tax_slab_pct%      (ESTIMATE only)
    post_tax_income   = net_income − estimated_tax

Note on dp_charge: Zerodha charges a flat depository (DP) fee per scrip (₹13)
plus GST, whatever the fee earned (Phase 1.4; it was a percent of gross before).
``dp_charge`` is reported GST-inclusive (₹15.34), so a small parcel can net
below zero. Reference: 100 shares at ₹13 → 1300 − 260 − 46.80 − 15.34 = 977.86.

Because each figure is rounded independently from the unrounded intermediates,
the displayed components may differ from the displayed net by at most one paisa;
every individual figure is nonetheless correctly rounded. This is standard for
per-line financial display and avoids compounding rounding error.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from slbm_tracker.db.models import LendingContract, PnLRecord, Stock
from slbm_tracker.services import business_config
from slbm_tracker.utils.money import apply_percent, money_to_float, to_decimal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PnLBreakdown:
    """A fully-itemized lending P&L breakdown (all money values in INR, 2dp).

    Carries both the resulting figures and the exact inputs/rates used, so the
    calculation is transparent and reproducible.
    """

    # Inputs
    quantity_lent: int
    lending_fee: float  # per share
    # Rates actually applied (percent)
    processing_fee_pct: float
    gst_pct: float
    dp_charge_flat_inr: float  # per scrip, before GST
    tax_slab_pct: float
    # Components (money totals, each rounded to paise)
    gross_fee: float
    processing_charge: float
    gst: float
    dp_charge: float
    net_income: float
    estimated_tax: float
    post_tax_income: float


class PnLService:
    """Deterministic P&L computation + persistence for lending contracts."""

    @staticmethod
    def _rates(
        processing_pct: Optional[float],
        gst_pct: Optional[float],
        dp_flat_inr: Optional[float],
        tax_slab_pct: Optional[float],
    ) -> tuple[float, float, float, float]:
        """Resolve applied rates, falling back to the effective configured defaults."""
        return (
            business_config.effective("broker_processing_fee_pct") if processing_pct is None else processing_pct,
            business_config.effective("gst_pct") if gst_pct is None else gst_pct,
            business_config.effective("dp_charge_flat_inr") if dp_flat_inr is None else dp_flat_inr,
            business_config.effective("default_tax_slab_pct") if tax_slab_pct is None else tax_slab_pct,
        )

    @classmethod
    def compute_breakdown(
        cls,
        quantity_lent: int,
        lending_fee: float,
        *,
        processing_pct: Optional[float] = None,
        gst_pct: Optional[float] = None,
        dp_flat_inr: Optional[float] = None,
        tax_slab_pct: Optional[float] = None,
    ) -> PnLBreakdown:
        """Pure, DB-free P&L calculation. Deterministic and unit-testable."""
        if quantity_lent < 0:
            raise ValueError("quantity_lent cannot be negative.")
        if lending_fee < 0:
            raise ValueError("lending_fee cannot be negative.")

        p_pct, g_pct, d_flat, t_pct = cls._rates(
            processing_pct, gst_pct, dp_flat_inr, tax_slab_pct
        )

        # Unrounded Decimal intermediates.
        gross = to_decimal(quantity_lent) * to_decimal(lending_fee)
        processing = apply_percent(gross, p_pct)
        gst = apply_percent(processing, g_pct)
        # Flat per scrip, GST on top; nothing lent means no DP debit.
        dp = (
            to_decimal(d_flat) + apply_percent(to_decimal(d_flat), g_pct)
            if quantity_lent > 0
            else to_decimal(0)
        )
        net = gross - processing - gst - dp
        # Tax is estimated only on positive net income. A non-positive net (costs
        # meeting/exceeding the gross fee under a pathological rate config) yields
        # zero estimated tax — never a negative "tax credit", which would push
        # post-tax ABOVE net. Clamp here rather than at display so post_tax stays
        # exactly ``net`` when there is nothing to tax.
        tax = apply_percent(net, t_pct) if net > 0 else to_decimal(0)
        post_tax = net - tax

        # Each figure independently rounded to paise for storage/display.
        return PnLBreakdown(
            quantity_lent=int(quantity_lent),
            lending_fee=float(lending_fee),
            processing_fee_pct=float(p_pct),
            gst_pct=float(g_pct),
            dp_charge_flat_inr=float(d_flat),
            tax_slab_pct=float(t_pct),
            gross_fee=money_to_float(gross),
            processing_charge=money_to_float(processing),
            gst=money_to_float(gst),
            dp_charge=money_to_float(dp),
            net_income=money_to_float(net),
            estimated_tax=money_to_float(tax),
            post_tax_income=money_to_float(post_tax),
        )

    @classmethod
    def record_for_contract(
        cls,
        db: Session,
        contract_id: int,
        *,
        tax_slab_pct: Optional[float] = None,
    ) -> PnLRecord:
        """Compute and persist the P&L record for a contract (idempotent upsert).

        Uses the contract's own quantity_lent × lending_fee. Re-running updates
        the single record (PnLRecord.contract_id is unique) rather than adding a
        duplicate.
        """
        contract = (
            db.query(LendingContract).filter(LendingContract.id == contract_id).first()
        )
        if contract is None:
            raise ValueError(f"Contract {contract_id} not found.")

        b = cls.compute_breakdown(
            contract.quantity_lent, contract.lending_fee, tax_slab_pct=tax_slab_pct
        )

        record = (
            db.query(PnLRecord).filter(PnLRecord.contract_id == contract_id).first()
        )
        if record is None:
            record = PnLRecord(contract_id=contract_id)
            db.add(record)

        record.gross_fee = b.gross_fee
        record.processing_charge = b.processing_charge
        record.gst = b.gst
        record.dp_charge = b.dp_charge
        record.net_income = b.net_income
        record.tax_slab_pct = b.tax_slab_pct
        record.estimated_tax = b.estimated_tax
        record.post_tax_income = b.post_tax_income

        db.commit()
        db.refresh(record)
        return record

    @staticmethod
    def get_for_contract(db: Session, contract_id: int) -> Optional[PnLRecord]:
        return (
            db.query(PnLRecord).filter(PnLRecord.contract_id == contract_id).first()
        )

    @staticmethod
    def list_records(db: Session) -> list[tuple[PnLRecord, str, Optional[str]]]:
        """All P&L records joined to their contract's symbol + series."""
        return (
            db.query(PnLRecord, Stock.symbol, LendingContract.series)
            .join(LendingContract, PnLRecord.contract_id == LendingContract.id)
            .join(Stock, LendingContract.stock_id == Stock.id)
            .order_by(PnLRecord.created_at.desc())
            .all()
        )
