"""Underlying equity quotes omitted from the SLB feed, with their own timestamps.

The ETF market watch is fetched once per sweep. Other missing symbols use NSE's
quote-page API. Only monitored holdings are retained, and every request uses the
scheduler's shared limiter and first-403 backoff.
"""
from __future__ import annotations
import datetime as dt
import json
from pathlib import Path
from slbm_tracker.config import get_settings
from slbm_tracker.services.contract_service import IST
from slbm_tracker.services.nse_client import NSEBlockedError, NSEError
from slbm_tracker.services.nse_parser import NSEDataParser


def cache_path():
    return Path(get_settings().history_cache_dir)/'equity-quotes.json'


def read_quotes():
    try: return json.loads(cache_path().read_text())
    except (OSError,ValueError): return {}


def timestamp(raw):
    for fmt in ('%d-%b-%Y %H:%M:%S','%d-%b-%Y %H:%M','%d-%m-%Y %H:%M:%S'):
        try: return dt.datetime.strptime(str(raw),fmt).replace(tzinfo=IST).astimezone(dt.timezone.utc).isoformat()
        except ValueError: pass
    return None


async def refresh_missing(client,symbols:set[str]):
    if not symbols: return 0
    quotes=read_quotes();updated=set()
    # Verified against NSE's public ETF market-watch JSON (ltP is market LTP, not NAV).
    etfs=await client.fetch_etf_quotes()
    if isinstance(etfs,dict):
        observed=timestamp(etfs.get('timestamp'))
        for row in etfs.get('data',[]):
            symbol=row.get('symbol');price=NSEDataParser.to_float(row.get('ltP'))
            if symbol in symbols and price>0 and observed:
                quotes[symbol]={'price':price,'timestamp':observed,'source':'NSE ETF market watch'};updated.add(symbol)
    for symbol in sorted(symbols-updated):
        try: raw=await client.fetch_equity_quote(symbol)
        except NSEBlockedError: raise
        except NSEError: continue
        if not isinstance(raw,dict):continue
        for row in raw.get('equityResponse',[]):
            if row.get('metaData',{}).get('symbol')!=symbol:continue
            price=NSEDataParser.to_float((row.get('tradeInfo')or{}).get('lastPrice'))
            observed=timestamp(row.get('lastUpdateTime'))
            if price>0 and observed:
                quotes[symbol]={'price':price,'timestamp':observed,'source':'NSE equity quote'};updated.add(symbol)
    path=cache_path();path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(quotes))
    return len(updated)
