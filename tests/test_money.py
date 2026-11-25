"""Unit tests for the Decimal money utility (rounding, float-safety)."""

from __future__ import annotations

from decimal import Decimal

from slbm_tracker.utils.money import (
    apply_percent,
    money_to_float,
    percent_ratio,
    quantize_money,
    to_decimal,
)


def test_to_decimal_avoids_float_binary_noise():
    # 0.1 as a raw Decimal(float) would be 0.1000000000000000055...; str() route avoids it.
    assert to_decimal(0.1) == Decimal("0.1")
    assert to_decimal(2850.5) == Decimal("2850.5")


def test_to_decimal_handles_none_and_garbage():
    assert to_decimal(None) == Decimal("0.00")
    assert to_decimal("not-a-number") == Decimal("0.00")
    assert to_decimal("not-a-number", Decimal("1")) == Decimal("1")


def test_quantize_money_rounds_half_up():
    # ROUND_HALF_UP: .005 rounds away from zero to .01 (banker's rounding would give .00).
    assert quantize_money(Decimal("1.005")) == Decimal("1.01")
    assert quantize_money(Decimal("2.675")) == Decimal("2.68")
    assert quantize_money(Decimal("0.014")) == Decimal("0.01")


def test_float_addition_problem_is_avoided():
    # The classic 0.1 + 0.2 != 0.3 float trap must not survive quantization.
    total = to_decimal(0.1) + to_decimal(0.2)
    assert quantize_money(total) == Decimal("0.30")


def test_percent_ratio_and_apply_percent():
    assert percent_ratio(18.0) == Decimal("0.18")
    # 20% of 100.00 == 20.00 (unrounded Decimal).
    assert apply_percent(Decimal("100.00"), 20.0) == Decimal("20.000")


def test_money_to_float_is_rounded_2dp():
    val = money_to_float(Decimal("123.456"))
    assert val == 123.46
    assert isinstance(val, float)
