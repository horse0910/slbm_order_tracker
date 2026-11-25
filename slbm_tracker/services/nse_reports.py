"""NSE CSV Reports fallback parser and report handler.

Provides offline and EOD fallback when live NSE JSON scraping is unavailable or blocked.
"""

from __future__ import annotations

import csv
import datetime
import io
import logging
import re
from typing import Optional

from slbm_tracker.services.nse_parser import MONTH_MAP, NSEDataParser, SLBMarketData

logger = logging.getLogger(__name__)


class NSEReportsService:
    """Handles parsing and ingestion of NSE EOD SLB CSV reports."""

    @classmethod
    def parse_slb_csv_report(
        cls,
        csv_text: str,
        report_date: Optional[datetime.date] = None,
    ) -> list[SLBMarketData]:
        """
        Parse NSE SLB CSV report format (e.g. daily trade reports or eligible securities).
        
        Handles common CSV header column variations from NSE reports:
        - Symbol / Security / UnderLying
        - Series / Contract Month
        - Expiry Date
        - LTP / Last Traded Price / Lending Fee / Close Price
        - High / Low / Open
        - Volume / Traded Qty
        - Open Positions / OI
        """
        results: list[SLBMarketData] = []
        if not csv_text or not csv_text.strip():
            return results

        f = io.StringIO(csv_text.strip())
        reader = csv.reader(f)

        rows = [row for row in reader if row and any(cell.strip() for cell in row)]
        if not rows:
            return results

        # Find header index
        header_idx = -1
        col_map: dict[str, int] = {}

        for i, row in enumerate(rows):
            normalized = [c.strip().lower() for c in row]
            if any(term in normalized for term in ("symbol", "security", "security name", "ticker")):
                header_idx = i
                for col_i, cell in enumerate(normalized):
                    col_map[cell] = col_i
                break

        if header_idx == -1:
            logger.warning("Could not find recognized header in SLB report CSV")
            return results

        def get_col(candidates: tuple[str, ...]) -> Optional[int]:
            for cand in candidates:
                for h_name, idx in col_map.items():
                    if cand == h_name or cand in h_name:
                        return idx
            return None

        sym_col = get_col(("symbol", "security", "security name", "ticker"))
        series_col = get_col(("series", "contract month", "series name"))
        expiry_col = get_col(("expiry", "expiry date", "exp_date"))
        ltp_col = get_col(("last traded price", "ltp", "lending fee", "close price", "settlement price", "fee"))
        bid_col = get_col(("bid", "best bid", "buy price"))
        ask_col = get_col(("ask", "best ask", "offer", "sell price"))
        vol_col = get_col(("volume", "qty", "traded qty", "traded quantity"))
        oi_col = get_col(("open positions", "open interest", "oi"))
        yield_col = get_col(("annualised yield", "yield", "yield per", "yield (%)"))

        for row in rows[header_idx + 1 :]:
            if sym_col is None or sym_col >= len(row):
                continue

            raw_sym = row[sym_col].strip().upper()
            if not raw_sym or raw_sym in ("TOTAL", "SYMBOL", "SECURITY"):
                continue

            series = row[series_col].strip() if series_col is not None and series_col < len(row) else ""
            
            # Parse expiry
            expiry_date: Optional[datetime.date] = None
            if expiry_col is not None and expiry_col < len(row):
                raw_exp = row[expiry_col].strip()
                for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
                    try:
                        expiry_date = datetime.datetime.strptime(raw_exp, fmt).date()
                        break
                    except ValueError:
                        pass

            if expiry_date is None and series:
                expiry_date = NSEDataParser.parse_series_expiry(series)

            ltp = NSEDataParser.to_float(row[ltp_col]) if ltp_col is not None and ltp_col < len(row) else 0.0
            best_bid = NSEDataParser.to_float(row[bid_col]) if bid_col is not None and bid_col < len(row) else 0.0
            best_ask = NSEDataParser.to_float(row[ask_col]) if ask_col is not None and ask_col < len(row) else 0.0
            volume = NSEDataParser.to_int(row[vol_col]) if vol_col is not None and vol_col < len(row) else 0
            open_pos = NSEDataParser.to_int(row[oi_col]) if oi_col is not None and oi_col < len(row) else 0
            ann_yield = NSEDataParser.to_float(row[yield_col]) if yield_col is not None and yield_col < len(row) else 0.0

            results.append(
                SLBMarketData(
                    symbol=raw_sym,
                    series=series,
                    expiry_date=expiry_date,
                    best_bid=best_bid,
                    best_bid_qty=0,
                    best_ask=best_ask,
                    best_ask_qty=0,
                    ltp=ltp,
                    underlying_price=0.0,
                    spread=round(best_ask - best_bid, 2) if best_ask > best_bid else 0.0,
                    spread_pct=0.0,
                    open_positions=open_pos,
                    volume=volume,
                    turnover=0.0,
                    annualized_yield=ann_yield,
                    # Ingestion time (naive-UTC), matching the live-API path's
                    # per-record timestamp. The old ``report_date @ 17:00`` was a
                    # naive-UTC stamp meant as IST close: ~5.5h skewed, and
                    # identical for every row in a report, which could out-sort
                    # fresher API rows and manufacture max-timestamp ties. Letting
                    # the default_factory apply utcnow() per record fixes both.
                )
            )

        return results
