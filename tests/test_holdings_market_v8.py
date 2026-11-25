"""Holdings market regressions: B-only, actual quote fields and Decimal income."""
import datetime as dt
from unittest.mock import AsyncMock
import pytest
from slbm_tracker.db.models import Stock, SLBSnapshot, LendingContract
from slbm_tracker.services import live_board_service
from slbm_tracker.services.contract_service import today_ist


def test_board_includes_name_underlying_and_net_bid_return(db):
    today=today_ist()
    stock=Stock(symbol='TEST',name='Test Industries',quantity=100,avg_buy_price=100)
    db.add(stock);db.commit()
    db.add(SLBSnapshot(symbol='TEST',series='Dec-2026',series_code='XD',series_family='B',
        expiry_date=today+dt.timedelta(days=30),settlement_date=today+dt.timedelta(days=30),
        best_bid=13,best_bid_qty=100,best_ask=14,best_ask_qty=50,ltp=12,
        underlying_price=100,volume=200,open_positions=500,annualized_yield=1))
    db.commit()
    result=live_board_service.build_board(db)
    holding=result['holdings'][0];row=holding['series'][0]
    assert holding['name']=='Test Industries'
    assert row['underlying_price']==100
    assert row['net_fee']==977.86
    assert row['net_yield_pa']==118.97
    assert row['fillable']==100


def test_contract_fee_breakdown_is_pre_tax_and_decimal(client,db):
    stock=Stock(symbol='TEST',quantity=100);db.add(stock);db.commit()
    contract=LendingContract(stock_id=stock.id,quantity_lent=100,lending_fee=13,
        expiry_date=today_ist()+dt.timedelta(days=30),trade_date=today_ist(),status='ACTIVE')
    db.add(contract);db.commit()
    row=client.get('/api/contracts').json()[0]
    assert row['estimated_charges']==322.14
    assert row['net_income']==977.86
    assert row['lending_fee_income']==1300


def test_scheduler_status_read_does_not_request_nse(client):
    result=client.get('/api/scheduler/status')
    assert result.status_code==200
    assert result.json()['refresh_seconds']>=30


def test_history_analytics_clearly_reports_missing_365_days(client):
    result=client.get('/api/analytics/slb-trades?days=365')
    assert result.status_code==200
    data=result.json()
    assert data['days']==365
    assert data['rows']==[]
    assert data['coverage']['complete'] is False
    assert data['granularity']=='daily_contract_aggregate'

@pytest.mark.asyncio
async def test_batch_by_expiry_keeps_only_holdings_and_series_b(db):
    from slbm_tracker.config import Settings
    from slbm_tracker.services.nse_client import NSEClient
    from slbm_tracker.services.scheduler_service import AlertSchedulerService
    from slbm_tracker.services.contract_service import IST
    db.add(Stock(symbol='IRFC',quantity=100));db.commit()
    client=AsyncMock(spec=NSEClient)
    client.fetch_series_master.return_value={'data':{'Series A':[{'key':'11','value':'Nov-2026'}],
        'Series B':[{'key':'XN','value':'Nov-2026'},{'key':'N1','value':'Nov-2026(N1)**'}]}}
    client.fetch_slb_by_series.return_value={'timestamp':'26-Oct-2026 11:00:00','data':[
        {'symbol':'IRFC','buyOrderPrice1':1,'buyOrderQty1':100,'lastTradedPrice':0.5,'underLyingLtp':100},
        {'symbol':'NOTOWNED','buyOrderPrice1':99,'buyOrderQty1':100}]}
    now=dt.datetime(2026,10,26,11,tzinfo=IST)
    scheduler=AlertSchedulerService(Settings(scheduler_batch_series_enabled=True),client,clock=lambda:now)
    assert await scheduler._refresh_market_data(db,now)==1
    client.fetch_slb_by_series.assert_awaited_once_with('XN')
    snapshots=db.query(SLBSnapshot).all()
    assert len(snapshots)==1 and snapshots[0].symbol=='IRFC'
    assert snapshots[0].series_family=='B' and snapshots[0].series_code=='XN'
    assert snapshots[0].timestamp==dt.datetime(2026,10,26,5,30)


def test_actual_archive_parser_is_b_only_and_preserves_quantities():
    from slbm_tracker.services.slb_history_service import parse_bhavcopy
    text=('TEST INDUSTRIES,TEST,XD,01-DEC-2026,N,2,1,3,0.5,2, ,100,150,3,0.5,23-OCT-2026,4\n'
          'TEST INDUSTRIES,TEST,12,01-DEC-2026,N,2,1,3,0.5,2, ,100,150,3,0.5,23-OCT-2026,4\n')
    rows=parse_bhavcopy(text,dt.date(2026,10,23),{'TEST'})
    assert len(rows)==1 and rows[0]['quantity']==100
    assert rows[0]['vwap']=='1.5000' and rows[0]['trades']==4


def test_archive_pagination_weighted_insights_and_missing_coverage(db,tmp_path,monkeypatch):
    import json
    from slbm_tracker.services import slb_history_service as history
    monkeypatch.setattr(history,'today_ist',lambda:dt.date(2026,10,26))
    db.add(Stock(symbol='TEST',quantity=100));db.commit()
    archive=history.SLBHistoryArchive(tmp_path)
    def row(day,code,qty,turnover):
        return dict(symbol='TEST',trade_date=day,settlement_date='2026-12-01' if code=='XD' else '2026-11-03',series_code=code,
                    quantity=qty,turnover=str(turnover),high=4,low=1,trades=2)
    for day,rows in [('2026-10-25',[row('2026-10-25','XD',100,200),row('2026-10-25','XN',150,150)]),
                     ('2026-10-24',[row('2026-10-24','XD',100,300)])]:
        (tmp_path/(day+'.json')).write_text(json.dumps(dict(available=True,rows=rows)))
    (tmp_path/'2026-10-23.json').write_text(json.dumps(dict(available=False,rows=[])))
    result=archive.read(db,days=3,offset=1,limit=1)
    assert result['offset']==1 and len(result['rows'])==1
    assert result['rows'][0]['series_code']=='XD'
    assert result['coverage']['retrieval_complete'] and not result['coverage']['complete']
    assert result['coverage']['unavailable_days']==1
    assert result['insights'][0]['volume_weighted_fee']==1.8571
    assert result['insights'][0]['recent_30_quantity']==350
    assert result['insights'][0]['observed_activity_pct']==100.0
    assert result['insights'][0]['most_active_expiry']=='2026-12-01'
    assert result['insights'][0]['most_active_series']=='XD'  # total series volume, not largest single row
    filtered=archive.read(db,days=3,expiry='2026-12-01')
    assert filtered['rows_total']==2 and len(filtered['expiries'])==2


@pytest.mark.asyncio
async def test_archive_import_caches_resumes_and_stops_on_first_block(db,tmp_path,monkeypatch):
    from slbm_tracker.services import slb_history_service as history
    from slbm_tracker.services.nse_client import NSEBlockedError
    monkeypatch.setattr(history,'today_ist',lambda:dt.date(2026,10,26))
    monkeypatch.setattr(history,'SessionLocal',lambda:db)
    monkeypatch.setattr(db,'close',lambda:None)
    db.add(Stock(symbol='TEST',quantity=100));db.commit()
    raw='TEST INDUSTRIES,TEST,XD,01-DEC-2026,N,2,1,3,0.5,2, ,100,150,3,0.5,25-OCT-2026,4\n'
    client=AsyncMock();client.request_text.side_effect=[raw,NSEBlockedError('blocked')]
    scheduler=type('Scheduler',(),{'_backoff_until':None})()
    archive=history.SLBHistoryArchive(tmp_path)
    await archive.start(client,scheduler,days=3);await archive.task
    assert client.request_text.await_count==2
    assert archive.status['processed']==1 and not archive.status['running']
    assert scheduler._backoff_until is not None and archive.status['error']
    # Resume reuses completed raw files, while retrying previously blocked days.
    client.reset_mock();client.request_text.side_effect=None;client.request_text.return_value=None
    scheduler._backoff_until=None
    await archive.start(client,scheduler,days=3);await archive.task
    assert client.request_text.await_count==2
    assert archive.status['processed']==3 and not archive.status['error']
    assert archive.read(db,days=3)['insights'][0]['quantity']==100


def test_keep_awake_owns_only_its_process(monkeypatch):
    from unittest.mock import Mock
    from slbm_tracker.services import power_service
    proc=Mock();proc.poll.return_value=None
    popen=Mock(return_value=proc)
    monkeypatch.setattr(power_service.sys,'platform','darwin')
    monkeypatch.setattr(power_service.subprocess,'Popen',popen)
    power=power_service.PowerService()
    assert not power.status()['enabled']
    assert power.set_enabled(True)['enabled']
    power.set_enabled(True);popen.assert_called_once()
    assert not power.set_enabled(False)['enabled']
    proc.terminate.assert_called_once()


@pytest.mark.asyncio
async def test_equity_fallback_filters_holdings_and_distinguishes_etf_ltp_from_nav(tmp_path,monkeypatch):
    import json
    from slbm_tracker.services import equity_quote_service as equity
    monkeypatch.setattr(equity,'cache_path',lambda:tmp_path/'equity.json')
    client=AsyncMock()
    client.fetch_etf_quotes.return_value={'timestamp':'26-Oct-2026 11:30:00','data':[
        {'symbol':'NIFTYBEES','ltP':'288.5','nav':'270'},
        {'symbol':'NOTHELD','ltP':'99'}]}
    client.fetch_equity_quote.return_value={'equityResponse':[{'metaData':{'symbol':'TATACHEM'},
        'tradeInfo':{'lastPrice':612.6},'lastUpdateTime':'26-Oct-2026 11:30:00'}]}
    assert await equity.refresh_missing(client,{'NIFTYBEES','TATACHEM'})==2
    quotes=json.loads((tmp_path/'equity.json').read_text())
    assert set(quotes)=={'NIFTYBEES','TATACHEM'} and quotes['NIFTYBEES']['price']==288.5
    assert quotes['TATACHEM']['timestamp']=='2026-10-26T06:00:00+00:00'
    client.fetch_equity_quote.assert_awaited_once_with('TATACHEM')


def test_board_falls_back_to_equity_cache_and_does_not_call_yesterdays_volume_today(db,monkeypatch):
    from slbm_tracker.services import equity_quote_service
    now=dt.datetime(2026,10,26,6)
    monkeypatch.setattr(live_board_service,'_utcnow',lambda:now)
    monkeypatch.setattr(equity_quote_service,'read_quotes',lambda:{'TEST':{'price':100,'timestamp':'2026-10-26T06:00:00+00:00','source':'NSE equity quote'}})
    db.add(Stock(symbol='TEST',quantity=100));db.commit()
    db.add(SLBSnapshot(symbol='TEST',series='Dec-2026',series_code='XD',series_family='B',
        expiry_date=dt.date(2026,12,1),settlement_date=dt.date(2026,12,1),timestamp=now-dt.timedelta(days=1),
        best_bid=1,best_bid_qty=100,underlying_price=0,volume=50))
    db.commit()
    row=live_board_service.build_board(db)['holdings'][0]['series'][0]
    assert row['underlying_price']==100 and row['underlying_source']=='NSE equity quote'
    assert row['traded_quantity']==0 and row['net_yield_pa'] is not None


def test_snapshot_analytics_excludes_series_a_and_special(db):
    from slbm_tracker.services.analytics_service import AnalyticsService
    now=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    for family,y in [('B',5),('A',90),('SPECIAL',99)]:
        db.add(SLBSnapshot(symbol='TEST',series_family=family,expiry_date=today_ist()+dt.timedelta(days=30),timestamp=now,annualized_yield=y))
    db.commit()
    assert AnalyticsService.yield_by_symbol(db)[0].latest_yield==5
    assert AnalyticsService.yield_history(db,'TEST',30)[0].samples==1


def test_bulk_monitor_rechecks_all_holdings_without_polling_tracked_symbols(db):
    from slbm_tracker.services.portfolio_service import PortfolioService
    now=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    db.add_all([Stock(symbol='IRFC',quantity=100,is_slb_eligible=False,slb_checked_at=now),Stock(symbol='UNOWNED',quantity=0)])
    db.commit()
    assert PortfolioService.polled_symbols(db,now)==[]
    assert PortfolioService.polled_symbols(db,now,include_ineligible=True)==['IRFC']


def test_stale_bid_has_no_executable_yield(db,monkeypatch):
    from slbm_tracker.config import get_settings
    monkeypatch.setattr(get_settings(),'opportunity_quote_max_age_seconds',180)
    now=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    snap=SLBSnapshot(symbol='TEST',series_code='XD',expiry_date=today_ist()+dt.timedelta(days=30),timestamp=now-dt.timedelta(minutes=4),underlying_price=100,best_bid=13,best_bid_qty=100)
    row=live_board_service._series_row(snap,now,100)
    assert row['is_stale'] and row['net_yield_pa'] is None


def test_underlying_price_uses_newest_observation_across_expiries(db,monkeypatch):
    now=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    monkeypatch.setattr(live_board_service,'_utcnow',lambda:now)
    monkeypatch.setattr(live_board_service.equity_quote_service,'read_quotes',lambda:{'TEST':{'price':95,'timestamp':(now-dt.timedelta(hours=1)).replace(tzinfo=dt.timezone.utc).isoformat(),'source':'NSE equity quote'}})
    db.add(Stock(symbol='TEST',quantity=100));db.commit()
    for code,price,at in [('XN',100,now),('XD',80,now-dt.timedelta(days=1))]:
        db.add(SLBSnapshot(symbol='TEST',series_code=code,series_family='B',expiry_date=today_ist()+dt.timedelta(days=30),underlying_price=price,timestamp=at))
    db.commit()
    rows=live_board_service.build_board(db)['holdings'][0]['series']
    assert all(r['underlying_price']==100 and r['underlying_updated_at']==now for r in rows)


def test_zero_session_fee_keeps_last_positive_fee_for_exact_expiry(db,monkeypatch):
    now=dt.datetime(2026,10,27,4)
    monkeypatch.setattr(live_board_service,'_utcnow',lambda:now)
    db.add(Stock(symbol='TEST',quantity=100));db.commit()
    for at,settlement,code,fee in [(now-dt.timedelta(days=1),dt.date(2026,12,1),'XD',0.45),
                                    (now-dt.timedelta(hours=2),dt.date(2027,12,7),'XD',9),
                                    (now,dt.date(2026,12,1),'XD',0)]:
        db.add(SLBSnapshot(symbol='TEST',series_code=code,series_family='B',expiry_date=settlement,
                           settlement_date=settlement,timestamp=at,ltp=fee,volume=0,best_bid=0.1,best_bid_qty=100,underlying_price=100))
    db.commit()
    row=live_board_service.build_board(db)['holdings'][0]['series'][0]
    assert row['ltp']==0.45
    assert row['fee_from_previous_observation']
    assert row['fee_observed_at']==now-dt.timedelta(days=1)
    assert row['traded_quantity']==0 and row['best_bid']==0.1
