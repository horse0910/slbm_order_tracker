"""Phase 3.1: lender economics in Decimal (ported from T's economics.py).

No minimum order value (decided 4 Oct 2026): T's min_order_value and
meets_minimum are left out. Charges come from business_config: processing 20%
of the fee, 18% GST on it, flat DP ₹13 + GST (₹15.34) per scrip.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from slbm_tracker.services import lending_economics as le
from slbm_tracker.services.pnl_service import PnLService


def test_zerodha_reference_example_nets_977_86():
    e = le.evaluate(100, 13, spot=1000, days=30)
    assert e.gross_fee == Decimal("1300.00")
    assert (e.processing, e.gst, e.dp) == (Decimal("260.00"), Decimal("46.80"), Decimal("15.34"))
    assert e.net_fee == Decimal("977.86")


def test_small_parcel_charges_exceed_the_fee():
    e = le.evaluate(50, Decimal("0.30"), spot=1200, days=30)
    assert e.gross_fee == Decimal("15.00")
    assert e.net_fee == Decimal("-3.88")
    assert e.net_yield_pa < 0


def test_yields_are_annualised_on_capital_at_the_spot_price():
    e = le.evaluate(100, 13, spot=1000, days=365)
    assert e.capital == Decimal("100000.00")
    assert e.gross_yield_pa == Decimal("1.30")
    assert e.net_yield_pa == Decimal("0.98")  # 977.86 / 100,000


def test_matches_the_pnl_engine():
    for qty, fee in [(100, 13), (50, 0.3), (4000, 3.1), (1, 0.05)]:
        b = PnLService.compute_breakdown(qty, fee, tax_slab_pct=0)
        assert le.evaluate(qty, fee, spot=100, days=30).net_fee == Decimal(str(b.net_income))


def test_no_minimum_order_value_anywhere():
    e = le.evaluate(1, Decimal("0.05"), spot=10, days=30)
    assert not hasattr(e, "meets_minimum")
    assert not hasattr(le.Charges(), "min_order_value")


def test_breakeven_price_nets_exactly_zero():
    p = le.breakeven_price(100)
    # 15.34 / (100 × 0.764) = 0.20078... -> rounded up to 0.2008 so it does not lose
    assert p == Decimal("0.2008")
    assert le.evaluate(100, p, spot=100, days=30).net_fee >= 0
    assert le.evaluate(100, p - Decimal("0.0001"), spot=100, days=30).net_fee < 0


@pytest.mark.parametrize("qty, spot, days", [(100, 1000, 30), (150, 77.06, 33), (4000, 154.2, 92), (25, 3800, 7)])
def test_price_for_target_yield_round_trips_to_exactly_6_percent_net(qty, spot, days):
    price = le.price_for_target_yield(qty, spot, days, target_pa=6)
    assert le.evaluate(qty, price, spot=spot, days=days).net_yield_pa == Decimal("6.00")


def test_degenerate_inputs():
    assert le.breakeven_price(0) == Decimal("0")
    assert le.price_for_target_yield(0, 100, 30, 6) == Decimal("0")
    assert le.price_for_target_yield(10, 0, 30, 6) == Decimal("0")
    e = le.evaluate(100, 1, spot=0, days=30)
    assert e.gross_yield_pa is None and e.net_yield_pa is None
    assert le.evaluate(0, 1, spot=100, days=30).dp == Decimal("0.00")
    assert le.evaluate(10, 1, spot=100, days=0).days == 1
