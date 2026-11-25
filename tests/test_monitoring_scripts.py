"""Monitoring helpers use temp files/DBs and saved responses, never the runtime."""
import datetime as dt
import importlib.util
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT/'scripts'/(name+'.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_daily_monitor_keeps_dates_separate_and_counts_only_todays_b_events(tmp_path, monkeypatch):
    module = script('monitor_today')
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    runtime = tmp_path/'runtime';runtime.mkdir()
    yesterday = runtime/'monitoring-2026-10-26.jsonl';yesterday.write_text('yesterday preserved\n')
    with sqlite3.connect(runtime/'slbm_tracker.db') as db:
        for table in ['slb_snapshots', 'alerts', 'lending_contracts']:
            db.execute('CREATE TABLE '+table+' (id INTEGER)')
        db.execute('CREATE TABLE slb_trade_events (quantity INTEGER,series_family TEXT,occurred_at TEXT)')
        db.executemany('INSERT INTO slb_trade_events VALUES (?,?,?)',[(100,'B','2026-10-27 03:00:00'),(999,'SPECIAL','2026-10-27 03:00:00'),(1000,'B','2026-10-26 03:00:00')])
        db.execute('CREATE TABLE notification_deliveries (channel TEXT,outcome TEXT,created_at TEXT)')
        db.executemany('INSERT INTO notification_deliveries VALUES (?,?,?)',[('macos','sent','2026-10-27 03:00:00'),('macos','sent','2026-10-26 03:00:00')])
    responses = {'/api/health':{'status':'healthy'}, '/api/scheduler/status':{'running':True,'power':{'enabled':True}},
                 '/api/portfolio/live':{'holdings':[]}, '/api/analytics/slb-trades?limit=1':{'coverage':{},'rows_total':0},
                 '/api/opportunities':{'stocks':[]}}
    monkeypatch.setattr(module,'get',lambda path:responses[path])
    data=module.sample(dt.datetime(2026,10,27,9,20,tzinfo=module.IST))
    assert data['inferred_events_today']==1 and data['inferred_quantity_today']==100
    assert data['deliveries']==[{'channel':'macos','outcome':'sent','count':1}]
    assert yesterday.read_text()=='yesterday preserved\n'
    assert (runtime/'monitoring-2026-10-27.jsonl').exists()
    assert (runtime/'monitor-state-2026-10-27.json').exists()


def comparison_fixture():
    holding={'symbol':'TEST','series':[{'series_code':'XD','series_family':'B','settlement_date':'2026-12-01','updated_at':'2026-10-27T03:45:00','ltp':1,'best_bid':1,'best_bid_qty':100,'best_ask':2,'best_ask_qty':200,'traded_quantity':50,'open_positions':500,'underlying_price':100}]}
    official={'source':'quote','blocked':False,'as_of':'27-Oct-2026 09:15:00','series':[{**holding['series'][0],'is_active':True}]}
    return holding,official


def test_verification_distinguishes_matching_quotes_from_time_skew():
    module=script('verify_nse');holding,official=comparison_fixture()
    assert module.compare(holding,official)['rows'][0]['status']=='matched'
    official['series'][0]['best_bid']=1.5
    assert module.compare(holding,official)['rows'][0]['status']=='same_timestamp_difference'
    official['as_of']='27-Oct-2026 09:16:00'
    row=module.compare(holding,official)['rows'][0]
    assert row['status']=='different_observation_times' and row['time_gap_seconds']==60
    holding['series'][0]['is_stale']=True
    assert module.compare(holding,official)['rows'][0]['status']=='cached_app_observation'


def test_settlement_mismatch_is_structural_and_cached_data_is_not_verified():
    module=script('verify_nse');holding,official=comparison_fixture()
    official['series'][0]['settlement_date']='2026-12-08'
    assert module.compare(holding,official)['rows'][0]['status']=='settlement_difference'
    official['source']='cache'
    assert module.compare(holding,official)['status']=='unverified_cached_or_blocked'


def test_verifier_ignores_other_families_and_does_not_compare_missing_stock_price():
    module=script('verify_nse');holding,official=comparison_fixture()
    official['series'].append({**official['series'][0],'series_code':'12','series_family':'A'})
    official['series'][0]['underlying_price']=0
    result=module.compare(holding,official)
    assert len(result['rows'])==1 and result['rows'][0]['status']=='matched'
