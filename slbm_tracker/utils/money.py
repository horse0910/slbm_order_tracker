"""Decimal-based money arithmetic for financial calculations.

Rationale
---------
Money must not be computed in binary floating point: ``0.1 + 0.2 != 0.3`` and
repeated fee/GST/tax steps accumulate materially wrong paise. All Phase 3
financial services (P&L, contract income) compute in :class:`decimal.Decimal`
and quantize to 2 places with ``ROUND_HALF_UP`` (standard commercial rounding)
only at the end.

The existing database columns are ``Float`` (Phase 1 schema) and are left
unchanged; services persist the *rounded Decimal result* cast to float, so the
stored value is already correctly rounded and never carries float noise beyond
2dp. Percentages (fee %, GST %, tax slab %) are kept as Decimal ratios during
computation and never rounded until they produce a money amount.

Units convention (documented once, referenced by every formula):
* money amounts (fees, income, charges, tax) — INR, 2 decimal places (paise).
* per-share lending fee — INR per share, kept at input precision until multiplied.
* percentages — whole-number percent inputs (e.g. 18.0 == 18%), converted to a
  ratio (0.18) via :func:`percent_ratio` before applying.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Union

Number = Union[int, float, str, Decimal, None]

# Smallest money unit: 2 decimal places (INR paise).
MONEY_QUANT = Decimal("0.01")
ZERO = Decimal("0.00")


def to_decimal(value: Number, default: Decimal = ZERO) -> Decimal:
    """Coerce any numeric-ish value to Decimal without float artifacts.

    Floats are routed through ``str`` so ``0.1`` becomes ``Decimal("0.1")``
    rather than the full binary expansion. ``None``/invalid → ``default``.

    Non-finite inputs (``NaN``, ``inf``) also fall back to ``default``: unlike
    a bad string these do NOT raise on construction (``Decimal(str(float("nan")))``
    yields ``Decimal("NaN")``), and would instead detonate later at
    ``.quantize()`` with ``InvalidOperation``. Guarding here keeps money
    arithmetic total and safe.
    """
    if value is None:
        return default
    if isinstance(value, Decimal):
        return value if value.is_finite() else default
    try:
        # str() first so floats don't leak binary noise into the Decimal.
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default
    return d if d.is_finite() else default


def quantize_money(value: Number) -> Decimal:
    """Round a Decimal money amount to 2 dp using ROUND_HALF_UP (commercial)."""
    return to_decimal(value).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def percent_ratio(percent: Number) -> Decimal:
    """Convert a whole-number percent (18.0) to a Decimal ratio (0.18)."""
    return to_decimal(percent) / Decimal("100")


def apply_percent(base: Number, percent: Number) -> Decimal:
    """Return ``base * percent%`` as an *unrounded* Decimal.

    Kept unrounded so callers can chain steps and quantize once at the end.
    """
    return to_decimal(base) * percent_ratio(percent)


def money_to_float(value: Number) -> float:
    """Quantize to 2dp and cast to float for persistence in Float columns.

    The value is already rounded to paise, so the resulting float carries no
    precision beyond 2 decimal places that could reintroduce drift.
    """
    return float(quantize_money(value))
