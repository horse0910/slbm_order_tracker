import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { liveApi } from '@/api/live';
import { useApiResource } from '@/hooks/useApiResource';
import { useWebSocket } from '@/hooks/useWebSocket';
import { Card, SectionHeader } from '@/components/Card';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/StateViews';
import { FreshnessIndicator } from '@/components/Freshness';
import { formatDate, formatDateTime, formatINR, formatPercent, formatPrice, formatQty } from '@/utils/format';
import { latestTimestamp } from '@/utils/freshness';
import type { LiveBoard, LiveHolding, LiveSeries, TradeFeed, TradeEvent } from '@/types';

type Filters={query:string;symbols:string[];expiry:string;bids:boolean;traded:boolean;positions:boolean;sort:string};
const DEFAULTS:Filters={query:'',symbols:[],expiry:'',bids:false,traded:false,positions:false,sort:'net'};
function saved():Filters {try{return {...DEFAULTS,...JSON.parse(localStorage.getItem('slbm-market-filters')||'{}')};}catch{return DEFAULTS;}}
export function formatAge(seconds:number){return seconds<60?`${seconds}s`:seconds<3600?`${Math.floor(seconds/60)}m`:seconds<86400?`${Math.floor(seconds/3600)}h`:`${Math.floor(seconds/86400)}d`;}
const fee=(v:number|null|undefined)=>v&&v>0?formatPrice(v):'—';
export function LiveBoardPage(){
 const timers=useRef<ReturnType<typeof setTimeout>[]>([]);
 const [pushed,setPushed]=useState<TradeEvent[]>([]);
 useEffect(()=>()=>timers.current.forEach(clearTimeout),[]);
 const board=useApiResource<LiveBoard>(s=>liveApi.board(s),[],{pollIntervalMs:60000});
 const feed=useApiResource<TradeFeed>(s=>liveApi.trades(s),[],{pollIntervalMs:60000});
 const [filters,setFilters]=useState<Filters>(saved),[page,setPage]=useState(1),[flash,setFlash]=useState<Record<string,boolean>>({});
 useEffect(()=>{localStorage.setItem('slbm-market-filters',JSON.stringify(filters));setPage(1);},[filters]);
 const update=(value:Partial<Filters>)=>setFilters(f=>({...f,...value}));
 const refresh=board.refetch,refreshFeed=feed.refetch;
 const onMessage=useCallback((raw:unknown)=>{const msg=raw as {type?:string;symbol?:string;series_code?:string};if(msg.type==='scheduler_tick'){refresh();refreshFeed();}if(msg.type==='market.trade'){const key=`${msg.symbol}-${msg.series_code}`;setFlash(f=>({...f,[key]:true}));timers.current.push(setTimeout(()=>setFlash(f=>({...f,[key]:false})),4000));setPushed(p=>[raw as TradeEvent,...p].slice(0,200));refresh();refreshFeed();}},[refresh,refreshFeed]);
 useWebSocket('/api/ws',{onMessage});
 const holdings=board.data?.holdings||[];
 const expiries=useMemo(()=>{const m=new Map<string,LiveSeries>();for(const h of holdings)for(const s of h.series)if(s.series_family==='B')m.set(s.series_code,s);return [...m.values()].sort((a,b)=>a.settlement_date.localeCompare(b.settlement_date));},[holdings]);
 const matches=(h:LiveHolding)=>(!filters.symbols.length||filters.symbols.includes(h.symbol))&&(`${h.symbol} ${h.name||''}`).toLowerCase().includes(filters.query.trim().toLowerCase());
 const allRows=holdings.flatMap(h=>h.series.filter(s=>s.series_family==='B').map(s=>({holding:h,series:s})));
 const rows=allRows.filter(({holding:h,series:s})=>matches(h)&&(!filters.expiry||s.series_code===filters.expiry)&&(!filters.bids||!s.is_stale&&!!s.best_bid&&!!s.best_bid_qty)&&(!filters.traded||(s.traded_quantity||0)>0)&&(!filters.positions||(s.open_positions||0)>0)).sort((a,b)=>filters.sort==='symbol'?a.holding.symbol.localeCompare(b.holding.symbol)||a.series.settlement_date.localeCompare(b.series.settlement_date):filters.sort==='volume'?(b.series.traded_quantity||0)-(a.series.traded_quantity||0):filters.sort==='oi'?(b.series.open_positions||0)-(a.series.open_positions||0):(b.series.net_yield_pa??-Infinity)-(a.series.net_yield_pa??-Infinity)||a.holding.symbol.localeCompare(b.holding.symbol));
 const pages=Math.max(1,Math.ceil(rows.length/25)),activePage=Math.min(page,pages),visible=rows.slice((activePage-1)*25,activePage*25);
 const noRows=holdings.filter(h=>matches(h)&&!h.series.some(s=>s.series_family==='B')&&!filters.bids&&!filters.traded&&!filters.positions);
 const notOnSlb=holdings.filter(h=>h.status==='not_on_slb');
 const seenEvents=new Set<string>();
 const tradeEvents=[...pushed,...(feed.data?.events||[])].filter(e=>{const k=`${e.symbol}-${e.series_code}-${e.occurred_at}`;if(seenEvents.has(k))return false;seenEvents.add(k);return true;}).filter(e=>e.series_family==='B'&&(!filters.symbols.length||filters.symbols.includes(e.symbol))&&(!filters.expiry||e.series_code===filters.expiry));
 const timestamp=latestTimestamp(allRows.map(r=>r.series.updated_at));
 return <div className="space-y-5">
  <section className="market-hero"><div><p className="eyebrow">YOUR SHARES. EVERY OPPORTUNITY.</p><h2>Your lending market<span>.</span></h2><p>Every holding, across every Series B expiry. See what borrowers are offering.</p></div><div className="hero-badge"><span className={`status-dot ${board.data?.market_open?'online':''}`}/>{board.data?.market_open?'Market open':'Market closed · latest quotes'}<small>NSE · SERIES B</small></div></section>
  <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
   {[['Holdings',holdings.length,'Held and actively lent'],['Series B contracts',allRows.length,'Across available expiries'],['With borrower bids',allRows.filter(r=>!r.series.is_stale&&!!r.series.best_bid&&!!r.series.best_bid_qty).length,'Current demand'],['Traded today',allRows.filter(r=>(r.series.traded_quantity||0)>0).length,'Contracts with reported volume']].map(([label,value,caption])=><Card key={label} className="metric-card"><p>{label}</p><strong>{board.data?formatQty(Number(value)):'—'}</strong><small>{caption}</small></Card>)}
  </div>
  <Card className="panel-pad">
   <div className="flex flex-wrap items-end gap-3">
    <div className="min-w-[180px] flex-1"><label className="label" htmlFor="holdings-search">Search holdings</label><input className="input" id="holdings-search" placeholder="Stock name or NSE symbol…" value={filters.query} onChange={e=>update({query:e.target.value})}/></div>
    <details className="stock-picker"><summary className="btn-ghost">{filters.symbols.length?`${filters.symbols.length} stocks selected`:'All stocks'} ▾</summary><div className="stock-picker-menu"><button className="text-accent text-xs" onClick={()=>update({symbols:[]})}>Select all holdings</button>{holdings.map(h=><label key={h.symbol}><input type="checkbox" checked={filters.symbols.includes(h.symbol)} onChange={e=>update({symbols:e.target.checked?[...filters.symbols,h.symbol]:filters.symbols.filter(s=>s!==h.symbol)})}/><span><b>{h.symbol}</b><small>{h.name||'NSE holding'}</small></span></label>)}</div></details>
    <div className="min-w-[185px]"><label className="label" htmlFor="expiry-filter">Expiry</label><select id="expiry-filter" className="input" value={filters.expiry} onChange={e=>update({expiry:e.target.value})}><option value="">All expiries</option>{expiries.map(s=><option key={s.series_code} value={s.series_code}>{s.series_code} - {(s.series||'').replace('-',' ')}</option>)}</select></div>
    <button className="btn-ghost" onClick={board.refetch} disabled={board.refreshing}>↻ Refresh</button><button className="btn-ghost" onClick={()=>setFilters({...DEFAULTS})}>Reset filters</button>
   </div>
   <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-3"><div className="flex flex-wrap gap-4 text-xs">{[['bids','Available borrower bids'],['traded','Trades today'],['positions','Open positions']].map(([key,label])=><label className="flex items-center gap-2" key={key}><input type="checkbox" checked={filters[key as 'bids'|'traded'|'positions']} onChange={e=>update({[key]:e.target.checked})}/>{label}</label>)}</div><div className="flex items-center gap-3"><select className="input !w-auto !py-1 text-xs" aria-label="Sort market" value={filters.sort} onChange={e=>update({sort:e.target.value})}><option value="net">Highest annualised net yield</option><option value="symbol">Stock A–Z</option><option value="volume">Highest traded quantity</option><option value="oi">Highest open positions</option></select><FreshnessIndicator timestamp={timestamp} lastUpdated={board.lastUpdated} refreshing={board.refreshing}/></div></div>
  </Card>
  {board.error?<ErrorState error={board.error} onRetry={board.refetch} compact/>:null}
  {board.data?.sweep_warning?<p className="notice-warning">{board.data.sweep_warning}</p>:null}
  <Card><SectionHeader title="Holdings × expiry" subtitle={`Series B only · ${rows.length} matching contracts · updated every ${board.data?.refresh_seconds||60}s`}/>
   {board.loading?<TableSkeleton rows={8} cols={10}/>:<>
    <div className="market-table-scroll"><table className="w-full border-collapse"><thead><tr>{['Stock','Expiry','Stock LTP','Fee LTP','Borrower bid (qty)','Lender offer (qty)','Traded today','Open positions','Net yield p.a.','Age'].map(t=><th className="th" key={t}>{t}</th>)}</tr></thead><tbody>
     {visible.map(({holding:h,series:s})=>{const key=`${h.symbol}-${s.series_code}`;return <tr key={key} data-testid={`row-${key}`} data-flash={flash[key]?'true':'false'} className={`border-b border-line/60 ${flash[key]?'bg-accent-soft':''}`}><td className="td"><Link className="font-semibold hover:text-accent" to={`/slb/${h.symbol}`}>{h.symbol}</Link><small className="cell-caption max-w-[180px] truncate">{h.name||`${formatQty(h.total_owned)} shares owned`}</small></td><td className="td"><b className="expiry-pill">{s.series_code}</b><small className="cell-caption">{(s.series||'').replace('-',' ')}</small><small className="cell-caption" title={s.settlement_estimated?'Calendar fallback':'Settlement date'}>{formatDate(s.settlement_date)}{s.settlement_estimated?' ~':''}</small></td><td className="td tnum" title={`${s.underlying_source||"NSE SLB feed"} · ${formatDateTime(s.underlying_updated_at||s.updated_at)} IST`}>{fee(s.underlying_price)}{s.underlying_source&&s.underlying_source!=="NSE SLB feed"?<small className="cell-caption">Equity quote</small>:null}</td><td className="td tnum" title={`Fee last reported ${formatDateTime(s.fee_observed_at||s.updated_at)} IST; this is an observation time`}>{fee(s.ltp)}<small className="cell-caption">{s.ltp?(s.fee_from_previous_observation?'Last reported fee':s.traded_quantity?'Traded today':'No trades today'):'No last trade'}</small></td><td className="td tnum text-pos">{s.best_bid?`${fee(s.best_bid)} (${formatQty(s.best_bid_qty||0)})`:'—'}{s.is_stale&&s.best_bid?<small className="cell-caption">Cached bid</small>:null}</td><td className="td tnum text-warn">{s.best_ask?`${fee(s.best_ask)} (${formatQty(s.best_ask_qty||0)})`:'—'}</td><td className="td tnum">{formatQty(s.traded_quantity||0)}</td><td className="td tnum">{formatQty(s.open_positions||0)}</td><td className="td tnum"><strong className="text-accent">{s.net_yield_pa!=null?formatPercent(s.net_yield_pa):'—'}</strong><small className="cell-caption">{s.net_fee!=null?`${formatINR(s.net_fee)} net · ${formatQty(s.fillable)} fill`:'No executable yield'}</small></td><td className={`td tnum ${s.age_seconds>180?'text-warn':'text-slate-500'}`}>{formatAge(s.age_seconds)}</td></tr>;})}
    </tbody></table></div>
    {!rows.length?<EmptyState title="No matching Series B contracts" message="Clear a filter, choose another expiry, or wait for the next scheduled sweep."/>:null}
    <div className="table-pagination"><span>{rows.length?`${(activePage-1)*25+1}–${Math.min(activePage*25,rows.length)}`:'0'} of {rows.length} contracts</span><div><button className="btn-ghost" disabled={activePage<=1} onClick={()=>setPage(activePage-1)}>← Previous</button><span>{activePage} / {pages}</span><button className="btn-ghost" disabled={activePage>=pages} onClick={()=>setPage(activePage+1)}>Next →</button></div></div>
   </>}
  </Card>
  {noRows.length?<Card className="panel-pad"><h3 className="mb-3 text-sm font-semibold">Holdings without available Series B quotes</h3><div className="flex flex-wrap gap-2">{noRows.map(h=><Link key={h.symbol} to={`/slb/${h.symbol}`} className="holding-chip">{h.symbol}<small>{h.status==='not_on_slb'?'Not on SLB board':'No quote supplied'}</small></Link>)}</div></Card>:null}
  {notOnSlb.length?<div data-testid="not-on-slb" className="text-xs text-slate-500">Not on the SLB board: {notOnSlb.map(h=><span key={h.symbol} className="mr-2">{h.symbol}</span>)}.</div>:null}
  <div data-testid="trade-feed"><Card><SectionHeader title="Today's inferred trading activity" subtitle="Quantity increases since last refresh, not an execution-level trade tape."/>{tradeEvents.length?<div className="grid gap-3 p-4 md:grid-cols-2 lg:grid-cols-3">{tradeEvents.slice(0,6).map((e,i)=><div key={`${e.symbol}-${e.occurred_at}-${i}`} className="rounded-lg border border-line p-3"><p className="text-sm font-semibold">{e.symbol} <span className="text-accent">{e.series_code}</span></p><p className="mt-1 text-xs">+{formatQty(e.quantity)} @ ₹{fee(e.last_fee)}</p><p className="mt-2 text-2xs text-slate-500">Inferred · {formatDateTime(e.occurred_at)} IST</p></div>)}</div>:<EmptyState title="No inferred trades yet today" message="Activity appears when reported traded quantity rises between two sweeps."/>}</Card></div>
  <p className="text-2xs text-slate-500">Stock LTP is the equity price; fee LTP is the last lending fee. A dash means NSE did not supply a price. Net yield uses the current borrower bid, fillable quantity and configured charges, before tax. Your holdings are monitored; no orders are placed.</p>
 </div>;
}
