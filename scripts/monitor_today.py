"""One date-scoped, cache-only health sample for the current IST trading day.

No upstream NSE requests or orders. The heartbeat owns scheduling and its own
15:30 cutoff. This script is also safe to run by hand to inspect current state.
"""
import datetime as dt
import json
import sqlite3
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
IST=dt.timezone(dt.timedelta(hours=5,minutes=30))


def get(path):
    with urllib.request.urlopen('http://127.0.0.1:8010'+path,timeout=5) as response:
        return json.load(response)


def sample(now=None):
    now=now or dt.datetime.now(IST)
    day=now.date().isoformat()
    health=get('/api/health');scheduler=get('/api/scheduler/status')
    board=get('/api/portfolio/live');history=get('/api/analytics/slb-trades?limit=1')
    opportunities=get('/api/opportunities')
    rows=[row for h in board['holdings'] for row in h['series'] if row['series_family']=='B']
    qualifying=[dict(symbol=s['symbol'],series_code=o['series_code'],net_yield_pa=o['net_yield_pa'],net_fee=o['net_fee'])
                for s in opportunities.get('stocks',[]) for o in s['options'] if o['actionable']]
    since=dt.datetime.combine(now.date(),dt.time(),tzinfo=IST).astimezone(dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    with sqlite3.connect('file:'+str(ROOT/'runtime'/'slbm_tracker.db')+'?mode=ro',uri=True) as db:
        counts={table:db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
                for table in ['slb_snapshots','slb_trade_events','alerts','lending_contracts']}
        trades=db.execute("SELECT COUNT(*),COALESCE(SUM(quantity),0) FROM slb_trade_events WHERE series_family='B' AND occurred_at>=?",(since,)).fetchone()
        deliveries=[dict(channel=c,outcome=o,count=n) for c,o,n in db.execute(
            'SELECT channel,outcome,COUNT(*) FROM notification_deliveries WHERE created_at>=? GROUP BY channel,outcome',(since,))]
    result={'at':now.isoformat(),'health':health['status'],'scheduler_running':scheduler['running'],
            'last_sweep':scheduler.get('last_sweep'),'backoff_until':scheduler.get('backoff_until'),
            'keep_awake':scheduler['power']['enabled'],'holdings':len(board['holdings']),
            'series_b_rows':len(rows),'fresh_rows':sum(not r.get('is_stale',False) for r in rows),
            'missing_underlying_symbols':sorted({h['symbol'] for h in board['holdings'] for r in h['series'] if r['series_family']=='B' and not r.get('underlying_price')}),
            'history_coverage':history['coverage'],'historical_daily_records':history['rows_total'],
            'inferred_events_today':trades[0],'inferred_quantity_today':trades[1],
            'qualifying_opportunities':qualifying,'counts':counts,'deliveries':deliveries}
    runtime=ROOT/'runtime';runtime.mkdir(exist_ok=True)
    with (runtime/('monitoring-'+day+'.jsonl')).open('a') as output:output.write(json.dumps(result)+'\n')
    (runtime/('monitor-state-'+day+'.json')).write_text(json.dumps(result,indent=2))
    (runtime/'monitor-state.json').write_text(json.dumps(result,indent=2))
    return result


if __name__=='__main__':print(json.dumps(sample(),indent=2))
