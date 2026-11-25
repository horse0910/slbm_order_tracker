"""Zerodha Holdings CSV Parser.

Isolates broker-specific holdings CSV export formats (Console, Kite, and generic fallback)
so broker logic can easily be swapped or extended without impacting core portfolio services.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from typing import Optional


# Header names for pledged / collateral shares (T kite.py), letters and digits only.
PLEDGED_ALIASES = frozenset({"collateralquantity", "pledgedquantity", "pledgedqty"})


@dataclass(frozen=True)
class ParsedHolding:
    """Represents a validated stock holding parsed from an external source."""

    symbol: str
    quantity: int
    avg_buy_price: float
    isin: Optional[str] = None
    name: Optional[str] = None
    notes: Optional[str] = None
    # Phase 3.2: shares pledged as collateral (cannot be lent until unpledged).
    pledged_quantity: int = 0


class ZerodhaHoldingsParser:
    """Parses holdings exported from Zerodha Console or Zerodha Kite."""

    SYMBOL_CLEAN_REGEX = re.compile(r"^(?:NSE|BSE):", re.IGNORECASE)

    @classmethod
    def clean_symbol(cls, raw_symbol: str) -> str:
        """Strip exchange prefixes (e.g., 'NSE:RELIANCE' -> 'RELIANCE') and normalize."""
        if not raw_symbol:
            return ""
        cleaned = cls.SYMBOL_CLEAN_REGEX.sub("", raw_symbol.strip())
        # Remove any whitespace and convert to uppercase
        return cleaned.strip().upper()

    @classmethod
    def parse_quantity(cls, raw_val: str) -> float:
        """Strict quantity parse: blank or '-' is 0, anything else unreadable raises.

        ``clean_number`` maps garbage to 0.0, which would make an unreadable
        quantity look like a sold holding (and, since Phase 1.6, zero it).
        """
        cleaned = str(raw_val or "").replace(",", "").strip()
        if not cleaned or cleaned == "-":
            return 0.0
        return float(cleaned)

    @classmethod
    def clean_number(cls, raw_val: str) -> float:
        """Parse numeric values that might contain commas, spaces, or currency symbols."""
        if not raw_val:
            return 0.0
        cleaned = (
            str(raw_val)
            .replace(",", "")
            .replace("₹", "")
            .replace("$", "")
            .strip()
        )
        if not cleaned or cleaned == "-":
            return 0.0
        try:
            return float(cleaned)
        except ValueError:
            return 0.0

    @classmethod
    def parse_csv(
        cls, csv_content: str
    ) -> tuple[list[ParsedHolding], list[str]]:
        """
        Parse CSV text content into a list of ParsedHolding items.
        
        Returns:
            tuple of (parsed_holdings, error_or_warning_messages)
        """
        holdings: list[ParsedHolding] = []
        errors: list[str] = []

        if not csv_content or not csv_content.strip():
            return holdings, ["CSV content is empty."]

        # Detect dialect or handle commas/tabs
        f = io.StringIO(csv_content.strip())
        reader = csv.reader(f)

        rows = [row for row in reader if row and any(cell.strip() for cell in row)]
        if not rows:
            return holdings, ["No valid rows found in CSV."]

        # Locate the header row (sometimes Zerodha adds disclaimer lines at top)
        header_idx = -1
        col_map: dict[str, int] = {}

        for i, row in enumerate(rows):
            normalized_row = [c.strip().lower() for c in row]
            # Check for header tokens
            has_symbol = any(
                token in normalized_row
                for token in ("instrument", "symbol", "stock", "trading symbol", "ticker")
            )
            has_qty = any(
                token in normalized_row
                for token in ("qty.", "qty", "quantity", "total quantity", "quantity available", "shares")
            )
            if has_symbol and has_qty:
                header_idx = i
                for col_i, cell in enumerate(normalized_row):
                    col_map[cell] = col_i
                break

        if header_idx == -1:
            # Try to treat first non-empty row as header
            header_row = [c.strip().lower() for c in rows[0]]
            for col_i, cell in enumerate(header_row):
                col_map[cell] = col_i
            header_idx = 0

        # Pledged / collateral shares (T's kite.py aliases), matched exactly on the
        # header's letters and digits. Found first and excluded from the other
        # lookups: 'quantity' is a substring of 'collateral quantity'.
        pledged_col = next(
            (
                idx
                for header_name, idx in col_map.items()
                if re.sub(r"[^a-z0-9]", "", header_name) in PLEDGED_ALIASES
            ),
            None,
        )

        # Helper to find column index matching candidate names
        def find_col(candidates: tuple[str, ...]) -> Optional[int]:
            for cand in candidates:
                for header_name, idx in col_map.items():
                    if idx == pledged_col:
                        continue
                    if cand == header_name or cand in header_name:
                        return idx
            return None

        sym_col = find_col(("instrument", "trading symbol", "symbol", "stock", "ticker"))
        qty_col = find_col(("qty.", "total quantity", "quantity available", "qty", "quantity", "shares"))
        price_col = find_col(("avg. cost", "average price", "avg cost", "avg price", "avg_buy_price", "buy price"))
        isin_col = find_col(("isin",))

        if sym_col is None or qty_col is None:
            return holdings, [
                f"Could not identify required 'Symbol' and 'Quantity' columns in header: {list(col_map.keys())}"
            ]

        # Process data rows
        for row_num, row in enumerate(rows[header_idx + 1 :], start=header_idx + 2):
            if not row or len(row) <= max(sym_col, qty_col):
                continue

            raw_sym = row[sym_col].strip() if sym_col < len(row) else ""
            symbol = cls.clean_symbol(raw_sym)

            # Skip header repeats, totals, or empty symbols
            if not symbol or symbol in ("TOTAL", "INSTRUMENT", "SYMBOL", "DISCLAIMER"):
                continue

            # Validate symbol characters
            if not re.match(r"^[A-Z0-9&\-_.]+$", symbol):
                errors.append(f"Row {row_num}: Invalid symbol format '{raw_sym}'. Skipped.")
                continue

            # Parse quantity
            raw_qty = row[qty_col].strip() if qty_col < len(row) else "0"
            try:
                qty_val = int(cls.parse_quantity(raw_qty))
            except (ValueError, TypeError):
                errors.append(f"Row {row_num}: Invalid quantity '{raw_qty}' for {symbol}. Skipped.")
                continue

            if qty_val <= 0:
                # Holding with 0 shares, skip or log
                continue

            pledged_val = 0
            if pledged_col is not None and pledged_col < len(row):
                raw_pledged = row[pledged_col].strip()
                try:
                    pledged_val = int(cls.parse_quantity(raw_pledged))
                except (ValueError, TypeError):
                    errors.append(f"Row {row_num}: Invalid pledged quantity '{raw_pledged}' for {symbol}. Skipped.")
                    continue
                pledged_val = min(max(pledged_val, 0), qty_val)

            # Parse average buy price
            avg_price = 0.0
            if price_col is not None and price_col < len(row):
                avg_price = cls.clean_number(row[price_col])

            # Parse ISIN if present
            isin = None
            if isin_col is not None and isin_col < len(row):
                raw_isin = row[isin_col].strip().upper()
                if raw_isin.startswith("IN") and len(raw_isin) == 12:
                    isin = raw_isin

            holdings.append(
                ParsedHolding(
                    symbol=symbol,
                    quantity=qty_val,
                    avg_buy_price=round(avg_price, 2),
                    isin=isin,
                    notes="Imported via Zerodha CSV",
                    pledged_quantity=pledged_val,
                )
            )

        return holdings, errors
