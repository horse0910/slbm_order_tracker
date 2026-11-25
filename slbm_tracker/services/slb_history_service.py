"""365-day NSE SLB bhavcopy archive, restricted to current holdings.

These are published daily contract aggregates, not an execution-level tape.
The DAT layout is the 17-column NSE SLBS-BHAVCOPY-DAT report verified against
NSE's /api/daily-reports?key=SLBS metadata. Missing reports remain explicit.
"""
from __future__ import annotations
import asyncio
import csv
import datetime as dt
import io
import json
import logging
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Optional

from slbm_tracker.config import get_settings
from slbm_tracker.db.session import SessionLocal
from slbm_tracker.db.models import Stock, LendingContract
from slbm_tracker.services.contract_service import today_ist, IST
from slbm_tracker.services.nse_client import NSEError, NSEBlockedError
from slbm_tracker.services.nse_parser import series_family
from slbm_tracker.utils.money import to_decimal, quantize_money

logger=logging.getLogger(__name__)
ARCHIVE_BASE='https://nsearchives.nseindia.com/archives/slbs/bhavcopy/'


def parse_bhavcopy(text: str, day: dt.date, symbols: set[str]) -> list[dict]:
    rows=[]
    for fields in csv.reader(io.StringIO(text)):
        if len(fields)<17: continue
        f=[v.strip() for v in fields]
        if f[1] not in symbols or series_family(f[2])!='B' or f[4]!='N': continue
        try:
            trade_day=dt.datetime.strptime(f[15],'%d-%b-%Y').date()
            settlement=dt.datetime.strptime(f[3],'%d-%b-%Y').date()
        except ValueError: continue
        if trade_day!=day: continue
        quantity=int(to_decimal(f[11]))
        turnover=to_decimal(f[12])
        if quantity<=0: continue
        rows.append({'symbol':f[1],'company_name':f[0],'series_code':f[2],
            'trade_date':day.isoformat(),'settlement_date':settlement.isoformat(),
            'open':float(to_decimal(f[6])),'high':float(to_decimal(f[7])),
            'low':float(to_decimal(f[8])),'close':float(to_decimal(f[9])),
            'quantity':quantity,'turnover':str(quantize_money(turnover)),
            'vwap':str((turnover/quantity).quantize(Decimal('0.0001'))),
            'trades':int(to_decimal(f[16])),'source':'NSE_SLBS_BHAVCOPY'})
    return rows


def owned_symbols(db) -> set[str]:
    lent=db.query(LendingContract.stock_id).filter(LendingContract.status=='ACTIVE')
    return {s.symbol for s in db.query(Stock).filter((Stock.quantity>0)|(Stock.id.in_(lent))).all()}


class SLBHistoryArchive:
    def __init__(self, root: Optional[Path]=None):
        self.root=Path(root or get_settings().history_cache_dir)
        self.task=None
        self.status={'running':False,'processed':0,'total':0,'available_reports':0,'unavailable_reports':0,'error':None}
        try:
            saved=json.loads((self.root/'sync-status.json').read_text())
            self.status.update(saved)
            if self.status.get('running'):
                self.status['error']='Import interrupted by an app restart. Resume to check remaining dates.'
            self.status['running']=False
        except (OSError,ValueError):pass

    async def start(self, client, scheduler=None, days=365):
        if self.task and not self.task.done(): return self.status
        self.root.mkdir(parents=True,exist_ok=True)
        self.status={'running':True,'processed':0,'total':days,'available_reports':0,'unavailable_reports':0,'error':None,
                     'started_at':dt.datetime.now(IST).isoformat()}
        self.task=asyncio.create_task(self._sync(client,scheduler,days))
        return self.status

    async def _sync(self,client,scheduler,days):
        db=SessionLocal()
        try: symbols=owned_symbols(db)
        finally: db.close()
        names={}
        end=today_ist()-dt.timedelta(days=1) # today's EOD file does not exist yet
        try:
            for offset in range(days):
                if scheduler and scheduler._backoff_until and dt.datetime.now(IST)<scheduler._backoff_until:
                    await asyncio.sleep(min(30,(scheduler._backoff_until-dt.datetime.now(IST)).total_seconds()))
                    while dt.datetime.now(IST)<scheduler._backoff_until: await asyncio.sleep(30)
                day=end-dt.timedelta(days=offset)
                dest=self.root/(day.isoformat()+'.json')
                raw_file=self.root/(day.isoformat()+'.DAT')
                if raw_file.exists():
                    text=raw_file.read_text(encoding='utf-8')
                else:
                    text=await client.request_text(ARCHIVE_BASE+'SLBM_BC_'+day.strftime('%d%m%Y')+'.DAT')
                if text is None:
                    # 404 may be a holiday OR an unavailable archive. Never infer zero trades.
                    self.status['unavailable_reports']+=1
                    dest.write_text(json.dumps({'date':day.isoformat(),'available':False,'rows':[]}))
                else:
                    if text.strip() and not any(len(r)>=17 for r in csv.reader(io.StringIO(text))):
                        raise NSEError('Unrecognized NSE SLB archive layout')
                    raw_file.write_text(text,encoding='utf-8')
                    rows=parse_bhavcopy(text,day,symbols)
                    for row in rows: names.setdefault(row["symbol"],row["company_name"])
                    (self.root/"names.json").write_text(json.dumps(names))
                    dest.write_text(json.dumps({'date':day.isoformat(),'available':True,'rows':rows}))
                    self.status['available_reports']+=1
                self.status['processed']+=1
                self.status['last_date']=day.isoformat()
                # Publish completed files incrementally; live polling shares the request lock.
                await asyncio.sleep(0)
        except NSEBlockedError:
            if scheduler: scheduler._backoff_until=dt.datetime.now(IST)+dt.timedelta(minutes=15)
            self.status['error']='NSE restricted archive access. Import paused; available history is retained. Retry after the 15-minute backoff.'
        except (NSEError,OSError) as exc:
            self.status['error']=f'History import stopped: {type(exc).__name__}. Available history is retained.'
            logger.warning('SLB archive import stopped: %s',exc)
        finally:
            self.status['running']=False
            self.status['finished_at']=dt.datetime.now(IST).isoformat()
            (self.root/'sync-status.json').write_text(json.dumps(self.status))

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            try: await self.task
            except asyncio.CancelledError: pass

    def read(self,db,symbol=None,days=365,offset=0,limit=100,expiry=None):
        symbols=owned_symbols(db)
        if symbol: symbols &= {symbol.strip().upper()}
        end=today_ist()-dt.timedelta(days=1)
        start=end-dt.timedelta(days=days-1)
        all_rows=[]; available=[]; missing=[]; pending=[]
        for day_offset in range(days):
            day=start+dt.timedelta(days=day_offset); file=self.root/(day.isoformat()+'.json')
            if not file.exists(): pending.append(day.isoformat()); continue
            try: data=json.loads(file.read_text())
            except (ValueError,OSError): pending.append(day.isoformat());continue
            if data.get('available'):
                available.append(day.isoformat())
                all_rows.extend(row for row in data['rows'] if row['symbol'] in symbols)
            else: missing.append(day.isoformat())
        expiries=sorted({r["settlement_date"] for r in all_rows})
        if expiry: all_rows=[r for r in all_rows if r["settlement_date"]==expiry]
        daily_groups=defaultdict(list)
        for row in all_rows: daily_groups[row["trade_date"]].append(row)
        daily=[]
        for date,day_rows in sorted(daily_groups.items()):
            qty=sum(r["quantity"] for r in day_rows)
            value=sum((to_decimal(r["turnover"]) for r in day_rows),Decimal(0))
            daily.append({"date":date,"quantity":qty,"trades":sum(r["trades"] for r in day_rows),"vwap":float((value/qty).quantize(Decimal("0.0001"))) if qty else None})
        grouped=defaultdict(list)
        for row in all_rows: grouped[row['symbol']].append(row)
        insights=[]
        for sym,rows in grouped.items():
            qty=sum(r['quantity'] for r in rows)
            value=sum((to_decimal(r['turnover']) for r in rows),Decimal(0))
            monthly=defaultdict(int)
            for r in rows: monthly[r['trade_date'][:7]]+=r['quantity']
            series_volume=defaultdict(int)
            for r in rows: series_volume[(r['series_code'],r['settlement_date'])]+=r['quantity']
            most_series,most_expiry=max(series_volume,key=series_volume.get)
            recent_start=end-dt.timedelta(days=29)
            recent=[r for r in rows if r['trade_date']>=recent_start.isoformat()]
            recent_qty=sum(r['quantity'] for r in recent)
            recent_value=sum((to_decimal(r['turnover']) for r in recent),Decimal(0))
            recent_reports=sum(day>=recent_start.isoformat() for day in available)
            observation_share=round(100*len({r['trade_date'] for r in rows})/len(available),1) if available else None
            insights.append({'symbol':sym,'trading_days':len({r['trade_date'] for r in rows}),
                'quantity':qty,'trade_count':sum(r['trades'] for r in rows),
                'volume_weighted_fee':float((value/qty).quantize(Decimal('0.0001'))) if qty else None,
                'highest_fee':max(r['high'] for r in rows),'lowest_fee':min(r['low'] for r in rows),
                'most_active_month':max(monthly,key=monthly.get),'most_active_series':most_series,'most_active_expiry':most_expiry,
                'observed_activity_pct':observation_share,'recent_30_quantity':recent_qty,
                'recent_30_available_reports':recent_reports,
                'recent_30_weighted_fee':float((recent_value/recent_qty).quantize(Decimal('0.0001'))) if recent_qty else None,
                'first_trade_date':min(r['trade_date'] for r in rows),'last_trade_date':max(r['trade_date'] for r in rows)})
        # A missing report can be a non-trading day; say 'retrieval complete', not 'all trades known'.
        return {'days':days,'from':start.isoformat(),'to':end.isoformat(),
            'granularity':'daily_contract_aggregate',
            'note':'NSE daily contract aggregates, not individual executions. Missing reports may be holidays or unavailable archives; they are not treated as zero trades. Historical lending fees do not establish a current executable yield.',
            'coverage':{'complete':not pending and not missing,'retrieval_complete':not pending,
                        'available_days':len(available),'unavailable_days':len(missing),'pending_days':len(pending),
                        'first_available':min(available) if available else None,'last_available':max(available) if available else None},
            'sync':dict(self.status),'insights':sorted(insights,key=lambda r:-r['quantity']),
            'daily':daily,'rows_total':len(all_rows),'offset':offset,'limit':limit,
            'expiries':expiries,
            'rows':sorted(all_rows,key=lambda r:(r['trade_date'],r['symbol'],r['series_code']),reverse=True)[offset:offset+limit if limit else None]}
