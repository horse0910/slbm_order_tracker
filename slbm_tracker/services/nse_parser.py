"""Structured parser for NSE SLB market data responses."""

from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from slbm_tracker.utils.money import to_decimal
from slbm_tracker.utils.slb_calendar import settlement_date_for_label

logger = logging.getLogger(__name__)

MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

FAMILY_A = "A"
FAMILY_B = "B"
FAMILY_SPECIAL = "SPECIAL"
# Series B codes (Zerodha lends Series B only): X1-X9, then XO / XN / XD.
SERIES_B_CODES = frozenset({f"X{m}" for m in range(1, 10)} | {"XO", "XN", "XD"})
_SERIES_A_CODE = re.compile(r"^\d{2}$")


def series_family(code: Optional[str]) -> str:
    """NSE series family from its code: '11' -> A, 'XO' -> B, anything else SPECIAL.

    SPECIAL covers R3, (O1), (O2), (ON), (OD) and any code not recognised; those
    are never offered as lendable (house rule 4).
    """
    code = (code or "").strip().upper()
    if _SERIES_A_CODE.match(code):
        return FAMILY_A
    if code in SERIES_B_CODES:
        return FAMILY_B
    return FAMILY_SPECIAL


@dataclass(frozen=True)
class SLBMarketData:
    """Strongly-typed parsed record representing an SLB contract quote/trade."""

    symbol: str
    series: str
    expiry_date: Optional[datetime.date]
    best_bid: float  # Borrower's bid price (lending fee)
    best_bid_qty: int
    best_ask: float  # Lender's asking price
    best_ask_qty: int
    ltp: float  # Last Traded Lending Fee
    underlying_price: float  # Underlying equity stock price
    spread: float
    spread_pct: float
    open_positions: int
    volume: int
    turnover: float
    annualized_yield: float  # % p.a.
    corporate_action: Optional[str] = None
    is_slb_eligible: bool = True
    # Phase 2.4: set from the quote API only. None for the symbol view and the CSV
    # report, which give a month label but no code, family or settlement date.
    series_code: Optional[str] = None
    series_family: Optional[str] = None
    settlement_date: Optional[datetime.date] = None
    settlement_estimated: Optional[bool] = None
    # Phase 3.3: ex-date of the corporate action, so only one inside the
    # lending period blocks an opportunity (stored in raw_data).
    corporate_action_ex_date: Optional[datetime.date] = None
    # Per-instance UTC timestamp. Must use default_factory (not a bare
    # ``datetime.utcnow()`` default, which is evaluated once at import and would
    # freeze every parsed record to process-start time).
    timestamp: datetime.datetime = field(default_factory=datetime.datetime.utcnow)


class NSEDataParser:
    """Parser for NSE SLB JSON responses and expiry calculations."""

    @staticmethod
    def calculate_first_tuesday(year: int, month: int) -> datetime.date:
        """
        Calculate the first Tuesday of a given month/year.
        Per SEBI/NSE SLB specifications, SLB contracts expire on the first Tuesday of the contract month.
        """
        first_day = datetime.date(year, month, 1)
        # weekday(): Monday is 0, Tuesday is 1
        days_ahead = (1 - first_day.weekday()) % 7
        return first_day + datetime.timedelta(days=days_ahead)

    @classmethod
    def parse_series_expiry(cls, series_str: str) -> Optional[datetime.date]:
        """
        Parse series string such as 'Mar-2027', 'Oct-2026(OD)', or month codes into the first Tuesday expiry date.
        """
        if not series_str:
            return None

        # Clean annotations like (OD) or (ON)
        clean = re.sub(r"\(.*?\)", "", series_str).strip()

        # Match 'Mon-YYYY' e.g. 'Mar-2027' or 'Oct-2026'
        match = re.search(r"([A-Za-z]{3})[-/\s]*(\d{4})", clean)
        if match:
            mon_str = match.group(1).upper()
            year_val = int(match.group(2))
            month_num = MONTH_MAP.get(mon_str)
            if month_num:
                return cls.calculate_first_tuesday(year_val, month_num)

        return None

    @classmethod
    def to_float(cls, val: Any, default: float = 0.0) -> float:
        """Safely convert any value to float."""
        if val is None or val == "" or val == "-":
            return default
        try:
            return float(str(val).replace(",", "").strip())
        except (ValueError, TypeError):
            return default

    @classmethod
    def to_int(cls, val: Any, default: int = 0) -> int:
        """Safely convert any value to integer."""
        if val is None or val == "" or val == "-":
            return default
        try:
            return int(cls.to_float(val, float(default)))
        except (ValueError, TypeError):
            return default

    @classmethod
    def parse_raw_slb_item(cls, item: dict[str, Any], default_symbol: str = "") -> Optional[SLBMarketData]:
        """Parse a single item from the NSE SLB JSON 'data' list."""
        if not isinstance(item, dict):
            return None

        # ``or default_symbol`` (not dict-get default) so an explicit JSON
        # ``null`` symbol coerces to the fallback rather than the string "NONE".
        symbol = str(item.get("symbol") or default_symbol).strip().upper()
        if not symbol:
            return None

        series = str(item.get("series", "")).strip()
        expiry_date = cls.parse_series_expiry(series)

        # In case caExpDate exists and series expiry isn't found
        if expiry_date is None and item.get("caExpDate"):
            try:
                # Format: '05-Jun-2026'
                parsed_d = datetime.datetime.strptime(str(item["caExpDate"]).strip(), "%d-%b-%Y").date()
                expiry_date = parsed_d
            except Exception:
                pass

        best_bid = cls.to_float(item.get("buyOrderPrice1"))
        best_bid_qty = cls.to_int(item.get("buyOrderQty1"))
        best_ask = cls.to_float(item.get("sellOrderPrice1"))
        # NSE's ask-quantity key is asymmetric: the bid side uses "buyOrderQty1"
        # but the ask side genuinely uses "sellQty1" (NOT "sellOrderQty1", which
        # does not exist in the payload). Verified against a live
        # /api/live-analysis-slb response — do not "symmetrize" this to
        # "sellOrderQty1" or best_ask_qty will silently read 0.
        best_ask_qty = cls.to_int(item.get("sellQty1"))
        ltp = cls.to_float(item.get("lastTradedPrice"))
        underlying_price = cls.to_float(item.get("underLyingLtp"))
        spread = cls.to_float(item.get("spread"))
        spread_pct = cls.to_float(item.get("spreadPer"))
        open_positions = cls.to_int(item.get("openPositions"))
        volume = cls.to_int(item.get("volume"))
        turnover = cls.to_float(item.get("turnOver"))
        annualized_yield = cls.to_float(item.get("annualisedYieldPer"))

        meta = item.get("meta", {})
        is_slb_eligible = True
        if isinstance(meta, dict) and "isSLBSec" in meta:
            is_slb_eligible = bool(meta.get("isSLBSec"))

        corporate_action = item.get("ca")
        if corporate_action:
            corporate_action = str(corporate_action).strip()

        return SLBMarketData(
            symbol=symbol,
            series=series,
            expiry_date=expiry_date,
            best_bid=best_bid,
            best_bid_qty=best_bid_qty,
            best_ask=best_ask,
            best_ask_qty=best_ask_qty,
            ltp=ltp,
            underlying_price=underlying_price,
            spread=spread,
            spread_pct=spread_pct,
            open_positions=open_positions,
            volume=volume,
            turnover=turnover,
            annualized_yield=annualized_yield,
            corporate_action=corporate_action,
            is_slb_eligible=is_slb_eligible,
        )

    @classmethod
    def parse_api_response(
        cls, response_data: dict[str, Any], default_symbol: str = ""
    ) -> list[SLBMarketData]:
        """Parse full NSE API response dictionary into a list of SLBMarketData objects."""
        results: list[SLBMarketData] = []
        if not isinstance(response_data, dict):
            return results

        raw_list = response_data.get("data")
        if not isinstance(raw_list, list):
            return results

        for item in raw_list:
            parsed = cls.parse_raw_slb_item(item, default_symbol=default_symbol)
            if parsed:
                results.append(parsed)

        return results

    # ---------- per-stock quote API (GetQuoteApi), Phase 2.3 ----------

    @staticmethod
    def parse_nse_date(value: Any) -> Optional[datetime.date]:
        """Parse NSE dates like '06-Oct-2026' or '03-AUG-2027'; None otherwise."""
        if not value:
            return None
        try:
            return datetime.datetime.strptime(str(value).strip().title(), "%d-%b-%Y").date()
        except ValueError:
            return None

    @staticmethod
    def compute_annualized_yield(
        fee: float,
        underlying_price: float,
        settlement: Optional[datetime.date],
        today: Optional[datetime.date] = None,
    ) -> float:
        """NSE's formula, in Decimal: fee / price × 365 / days to settlement × 100.

        0.0 when it cannot be computed (no fee, no price, no or past settlement):
        NSE sends a 0 underlying price on some series, and a yield computed on it
        would be meaningless.
        """
        if today is None:
            from slbm_tracker.services.contract_service import today_ist

            today = today_ist()
        fee_d, price_d = to_decimal(fee), to_decimal(underlying_price)
        if settlement is None or fee_d <= 0 or price_d <= 0:
            return 0.0
        days = (settlement - today).days
        if days <= 0:
            return 0.0
        value = fee_d / price_d * Decimal(365) / Decimal(days) * Decimal(100)
        return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    @classmethod
    def parse_quote_row(
        cls, row: dict[str, Any], today: Optional[datetime.date] = None
    ) -> Optional[dict[str, Any]]:
        """One series from getGetQuoteSLBMData.

        Keeps the series code and family (B collapsed both into the month label).
        Settlement is NSE's ``revLegSettDt``; without it, the calendar fallback
        (marked estimated when beyond the published table).
        """
        if not isinstance(row, dict) or not row.get("symbol"):
            return None
        code = str(row.get("series") or "").strip().upper()
        month = str(row.get("timeStamp") or "").strip()
        settlement = cls.parse_nse_date(row.get("revLegSettDt"))
        estimated: Optional[bool] = False if settlement else None
        if settlement is None:
            fallback = settlement_date_for_label(month)
            if fallback is not None:
                settlement, estimated = fallback

        def levels(price_key: str, qty_key: str) -> list[dict[str, float]]:
            out = []
            for i in range(1, 6):
                price = cls.to_float(row.get(f"{price_key}{i}"))
                qty = cls.to_int(row.get(f"{qty_key}{i}"))
                if price > 0 or qty > 0:
                    out.append({"price": price, "quantity": qty})
            return out

        bids = levels("buyPrice", "buyQuantity")
        offers = levels("sellPrice", "sellQuantity")
        ltp = cls.to_float(row.get("lastPrice"))
        underlying = cls.to_float(row.get("underLtp"))
        open_positions = cls.to_int(row.get("prevOutPos"))
        nse_yield = cls.to_float(row.get("annulisedYieldPer"))
        purpose = (row.get("purpose") or "").strip() or None
        ex_date = cls.parse_nse_date(row.get("exDt"))
        record_date = cls.parse_nse_date(row.get("recDt"))

        return {
            "symbol": str(row.get("symbol")).strip().upper(),
            "series_code": code,
            "series_family": series_family(code),
            "month": month,
            "settlement_date": settlement,
            "settlement_estimated": estimated,
            "bids": bids,
            "offers": offers,
            "best_bid": bids[0]["price"] if bids else 0.0,
            "best_bid_qty": bids[0]["quantity"] if bids else 0,
            "best_ask": offers[0]["price"] if offers else 0.0,
            "best_ask_qty": offers[0]["quantity"] if offers else 0,
            "total_buy_quantity": cls.to_int(row.get("totalBuyQuantity")),
            "total_sell_quantity": cls.to_int(row.get("totalSellQuantity")),
            "ltp": ltp,
            "open": cls.to_float(row.get("openingPrice")),
            "high": cls.to_float(row.get("highPrice")),
            "low": cls.to_float(row.get("lowPrice")),
            "close": cls.to_float(row.get("closePrice")),
            "average_price": cls.to_float(row.get("avgTradePrice")),
            "traded_quantity": cls.to_int(row.get("totTradedQty")),
            "traded_value": cls.to_float(row.get("ttlTrdValue")),
            "underlying_price": underlying,
            "spread": cls.to_float(row.get("spread")),
            "spread_pct": cls.to_float(row.get("pSpread")),
            "open_positions": open_positions,
            "annualized_yield": nse_yield
            or cls.compute_annualized_yield(ltp, underlying, settlement, today),
            "corporate_action": {
                "purpose": purpose,
                "ex_date": ex_date,
                "record_date": record_date,
            }
            if purpose
            else None,
            "margins": {
                "var_pct": cls.to_float(row.get("cmVarSecvar")),
                "var_margin_pct": cls.to_float(row.get("varMargin")),
                "extreme_loss_pct": cls.to_float(row.get("lossRate")),
                "adhoc_pct": cls.to_float(row.get("adhocMargin")),
                "total_pct": cls.to_float(row.get("marginRate")),
            },
            # Live: someone is quoting, trading or holding it.
            "is_active": bool(bids or offers or open_positions or ltp),
        }

    @classmethod
    def quote_row_to_market_data(cls, q: dict[str, Any], symbol: str) -> SLBMarketData:
        """A parsed quote series as a snapshot record, keeping code, family and settlement.

        ``series`` stays the month label so existing views line up; ``ltp`` is
        the last traded fee as NSE gives it (B substituted the close, which is
        not a traded fee).
        """
        return SLBMarketData(
            symbol=symbol.strip().upper(),
            series=q["month"],
            expiry_date=q["settlement_date"],
            best_bid=q["best_bid"],
            best_bid_qty=q["best_bid_qty"],
            best_ask=q["best_ask"],
            best_ask_qty=q["best_ask_qty"],
            ltp=q["ltp"],
            underlying_price=q["underlying_price"],
            spread=q["spread"],
            spread_pct=q["spread_pct"],
            open_positions=q["open_positions"],
            volume=q["traded_quantity"],
            turnover=q["traded_value"],
            annualized_yield=q["annualized_yield"],
            corporate_action=(q["corporate_action"] or {}).get("purpose"),
            corporate_action_ex_date=(q["corporate_action"] or {}).get("ex_date"),
            series_code=q["series_code"],
            series_family=q["series_family"],
            settlement_date=q["settlement_date"],
            settlement_estimated=q["settlement_estimated"],
        )

    @classmethod
    def parse_history_row(cls, row: dict[str, Any]) -> Optional[dict[str, Any]]:
        """One day × series from getSLBHistoricalData."""
        trade_date = cls.parse_nse_date(row.get("tradeDate")) if isinstance(row, dict) else None
        if trade_date is None:
            return None
        code = str(row.get("series") or "").strip().upper()
        return {
            "trade_date": trade_date,
            "settlement_date": cls.parse_nse_date(row.get("settlementDate")),
            "series_code": code,
            "series_family": series_family(code),
            "open": cls.to_float(row.get("openPrice")),
            "high": cls.to_float(row.get("highPrice")),
            "low": cls.to_float(row.get("lowPrice")),
            "close": cls.to_float(row.get("closePrice")),
            "previous_close": cls.to_float(row.get("previousClosePrice")),
            "quantity": cls.to_int(row.get("totalQuantity")),
            "trades": cls.to_int(row.get("totalTrades")),
            "value": cls.to_float(row.get("totalValue")),
            "high_52w": cls.to_float(row.get("high52Week")),
            "low_52w": cls.to_float(row.get("low52Week")),
        }
