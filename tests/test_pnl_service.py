"""Unit tests for the P&L engine: exact Decimal component values, separation of
components, idempotent persistence, and validation. Uses explicit rates so the
arithmetic is deterministic and independent of config drift.
"""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy.orm import Session

from slbm_tracker.db.models import PnLRecord, Stock
from slbm_tracker.services.contract_service import ContractService
from slbm_tracker.services.pnl_service import PnLService


def _stock(db: Session) -> Stock:
    stock = Stock(symbol="RELIANCE", quantity=1000, avg_buy_price=2500.0)
    db.add(stock)
    db.commit()
    db.refresh(stock)
    return stock


def test_zerodha_reference_example_with_configured_defaults():
    # Zerodha's own example: 100 shares at ₹13. Processing 20%, GST 18% on it,
    # DP flat ₹13 + 18% GST per scrip. Rates come from the configured defaults.
    b = PnLService.compute_breakdown(100, 13.0, tax_slab_pct=0.0)
    assert b.gross_fee == 1300.00
    assert b.processing_charge == 260.00
    assert b.gst == 46.80
    assert b.dp_charge == 15.34  # 13 + 18% GST
    assert b.net_income == 977.86
    assert b.dp_charge_flat_inr == 13.0


def test_breakdown_exact_components():
    # 100 shares × ₹5.00 fee, 20% processing, 18% GST, ₹13 + GST DP, 30% tax.
    b = PnLService.compute_breakdown(
        100, 5.00, processing_pct=20.0, gst_pct=18.0, dp_flat_inr=13.0, tax_slab_pct=30.0
    )
    assert b.gross_fee == 500.00
    assert b.processing_charge == 100.00  # 500 * 20%
    assert b.gst == 18.00  # 100 * 18%
    assert b.dp_charge == 15.34  # 13 * 1.18, independent of the fee
    assert b.net_income == 366.66  # 500 - 100 - 18 - 15.34
    assert b.estimated_tax == 110.00  # 366.66 * 30% = 109.998
    assert b.post_tax_income == 256.66


def test_dp_charge_is_flat_not_a_percent_of_the_fee():
    small = PnLService.compute_breakdown(10, 1.0, dp_flat_inr=13.0, gst_pct=18.0)
    large = PnLService.compute_breakdown(10000, 10.0, dp_flat_inr=13.0, gst_pct=18.0)
    assert small.dp_charge == large.dp_charge == 15.34


def test_charges_can_exceed_a_small_fee():
    # 10 × ₹2 = ₹20 gross; costs 4 + 0.72 + 15.34 = 20.06 -> net −0.06, no tax.
    b = PnLService.compute_breakdown(
        10, 2.0, processing_pct=20.0, gst_pct=18.0, dp_flat_inr=13.0, tax_slab_pct=30.0
    )
    assert b.gross_fee == 20.00
    assert b.processing_charge == 4.00
    assert b.gst == 0.72
    assert b.dp_charge == 15.34
    assert b.net_income == -0.06
    assert b.estimated_tax == 0.0


def test_no_dp_charge_when_nothing_is_lent():
    b = PnLService.compute_breakdown(0, 5.0, dp_flat_inr=13.0)
    assert b.dp_charge == 0.0 and b.net_income == 0.0


def test_breakdown_uses_decimal_half_up_on_gross():
    # 333 × 1.115 = 371.295 -> 371.30 (ROUND_HALF_UP), not 371.29.
    b = PnLService.compute_breakdown(333, 1.115, processing_pct=0.0, gst_pct=0.0, dp_flat_inr=0.0, tax_slab_pct=0.0)
    assert b.gross_fee == 371.30
    assert b.net_income == 371.30  # no costs
    assert b.estimated_tax == 0.0


def test_breakdown_rejects_negative():
    with pytest.raises(ValueError):
        PnLService.compute_breakdown(-1, 5.0)
    with pytest.raises(ValueError):
        PnLService.compute_breakdown(10, -5.0)


def test_tax_estimate_is_configurable():
    low = PnLService.compute_breakdown(100, 5.0, tax_slab_pct=0.0)
    high = PnLService.compute_breakdown(100, 5.0, tax_slab_pct=30.0)
    assert low.estimated_tax == 0.0
    assert low.post_tax_income == low.net_income
    assert high.estimated_tax > 0.0
    assert high.tax_slab_pct == 30.0


def test_record_for_contract_uses_contract_values_and_is_idempotent(db: Session):
    stock = _stock(db)
    contract = ContractService.create_contract(
        db=db, stock_id=stock.id, expiry_date=datetime.date(2026, 11, 3),
        quantity_lent=100, lending_fee=5.0, trade_date=datetime.date(2026, 10, 1),
    )
    rec1 = PnLService.record_for_contract(db=db, contract_id=contract.id)
    assert rec1.gross_fee == 500.00

    # Re-running upserts the SAME record (unique contract_id), no duplicate.
    rec2 = PnLService.record_for_contract(db=db, contract_id=contract.id)
    assert rec2.id == rec1.id
    assert db.query(PnLRecord).count() == 1


def test_record_for_missing_contract_raises(db: Session):
    with pytest.raises(ValueError, match="not found"):
        PnLService.record_for_contract(db=db, contract_id=4242)
