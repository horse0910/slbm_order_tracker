"""What a lending fee is worth after Zerodha's charges (Phase 3.1).

Ported from T's ``economics.py`` to Decimal (house rule 2). NSE's board shows a
yield on the gross fee; on Zerodha that is optimistic by about a quarter, and on
small parcels the flat DP charge alone can turn a positive screen into a loss.
Opportunities are scored on NET yield at the live bid (house rule 6).

Charges (from business_config, so the Settings page drives them):

* processing: ``broker_processing_fee_pct`` (20%) of the gross fee
* GST: ``gst_pct`` (18%) on the processing charge (23.6% haircut together)
* DP: flat ``dp_charge_flat_inr`` (₹13) + GST = ₹15.34 per scrip

No minimum order value is applied (decided 4 Oct 2026): T's
``min_order_value`` and ``meets_minimum`` are deliberately not ported.
Reference: 100 shares at ₹13 -> net ₹977.86.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Optional

from slbm_tracker.services import business_config
from slbm_tracker.utils.money import Number, quantize_money, to_decimal

_RATE = Decimal("0.01")
_PRICE = Decimal("0.0001")
_HUNDRED = Decimal(100)
_YEAR = Decimal(365)


@dataclass(frozen=True)
class Charges:
    """Zerodha's lending charges as percentages and a flat DP charge (INR)."""

    processing_pct: Decimal = Decimal("20")
    gst_pct: Decimal = Decimal("18")
    dp_flat_inr: Decimal = Decimal("13")

    @classmethod
    def from_config(cls) -> "Charges":
        return cls(
            processing_pct=to_decimal(business_config.effective("broker_processing_fee_pct")),
            gst_pct=to_decimal(business_config.effective("gst_pct")),
            dp_flat_inr=to_decimal(business_config.effective("dp_charge_flat_inr")),
        )

    @property
    def fee_haircut(self) -> Decimal:
        """Fraction of the gross fee lost to processing + GST (0.236)."""
        return self.processing_pct / _HUNDRED * (1 + self.gst_pct / _HUNDRED)

    @property
    def dp_total(self) -> Decimal:
        """Flat DP charge with GST, per scrip (₹15.34)."""
        return self.dp_flat_inr * (1 + self.gst_pct / _HUNDRED)


@dataclass(frozen=True)
class Economics:
    """One lending parcel: qty at ``lending_price`` per share until settlement."""

    qty: int
    lending_price: Decimal  # ₹ per share
    spot: Decimal  # underlying price: the capital at work
    days: int  # today to the reverse-leg settlement (at least 1)
    gross_fee: Decimal
    processing: Decimal
    gst: Decimal
    dp: Decimal
    net_fee: Decimal
    capital: Decimal
    gross_yield_pa: Optional[Decimal]  # None without a share price
    net_yield_pa: Optional[Decimal]
    breakeven_price: Decimal


def _yield(amount: Decimal, capital: Decimal, days: int) -> Optional[Decimal]:
    if capital <= 0:
        return None
    return (amount / capital * _YEAR / Decimal(days) * _HUNDRED).quantize(_RATE, rounding=ROUND_HALF_UP)


def evaluate(
    qty: int,
    lending_price: Number,
    spot: Number,
    days: int,
    charges: Optional[Charges] = None,
) -> Economics:
    """Gross, charges, net fee and annualised yields for one parcel (Decimal)."""
    c = charges or Charges.from_config()
    days = max(int(days), 1)
    price = to_decimal(lending_price)
    spot_d = to_decimal(spot)
    gross = price * qty
    processing = gross * c.processing_pct / _HUNDRED
    gst = processing * c.gst_pct / _HUNDRED
    dp = c.dp_total if qty > 0 else Decimal(0)
    net = gross - processing - gst - dp
    capital = spot_d * qty
    return Economics(
        qty=int(qty),
        lending_price=price,
        spot=spot_d,
        days=days,
        gross_fee=quantize_money(gross),
        processing=quantize_money(processing),
        gst=quantize_money(gst),
        dp=quantize_money(dp),
        net_fee=quantize_money(net),
        capital=quantize_money(capital),
        gross_yield_pa=_yield(gross, capital, days),
        net_yield_pa=_yield(net, capital, days),
        breakeven_price=breakeven_price(qty, c),
    )


def breakeven_price(qty: int, charges: Optional[Charges] = None) -> Decimal:
    """Lowest fee per share (4 dp, rounded up) at which the net fee is not negative."""
    c = charges or Charges.from_config()
    if qty <= 0:
        return Decimal(0)
    return (c.dp_total / (qty * (1 - c.fee_haircut))).quantize(_PRICE, rounding=ROUND_CEILING)


def price_for_target_yield(
    qty: int,
    spot: Number,
    days: int,
    target_pa: Number,
    charges: Optional[Charges] = None,
) -> Decimal:
    """Fee per share (4 dp, rounded up) you must be bid to clear ``target_pa``% net a year."""
    c = charges or Charges.from_config()
    spot_d = to_decimal(spot)
    if qty <= 0 or spot_d <= 0:
        return Decimal(0)
    days = max(int(days), 1)
    required_net = to_decimal(target_pa) / _HUNDRED * spot_d * qty * Decimal(days) / _YEAR
    gross = (required_net + c.dp_total) / (1 - c.fee_haircut)
    return (gross / qty).quantize(_PRICE, rounding=ROUND_CEILING)
