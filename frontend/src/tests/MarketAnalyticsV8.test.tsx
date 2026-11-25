import {beforeEach,describe,expect,it,vi} from 'vitest';
import {screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {AnalyticsPage} from '@/pages/AnalyticsPage';
import {TopBar} from '@/layouts/TopBar';
import {renderWithRouter,makeHolding} from './testUtils';
vi.mock('@/api/marketAnalytics',()=>({marketAnalyticsApi:{history:vi.fn(),refresh:vi.fn()}}));
vi.mock('@/api/portfolio',()=>({portfolioApi:{list:vi.fn()}}));
vi.mock('@/api/monitor',()=>({monitorApi:{status:vi.fn(),awake:vi.fn()}}));
vi.mock('@/api/dashboard',()=>({healthApi:{check:vi.fn()}}));
import {marketAnalyticsApi} from '@/api/marketAnalytics';
import {portfolioApi} from '@/api/portfolio';
import {monitorApi} from '@/api/monitor';
import {healthApi} from '@/api/dashboard';
beforeEach(()=>{
 vi.mocked(portfolioApi.list).mockResolvedValue([makeHolding({symbol:'IRFC',quantity:100}),makeHolding({id:2,symbol:'FULLYLENT',quantity:0,total_owned:10,lent:10,in_demat:0,lendable:0})]);
 vi.mocked(marketAnalyticsApi.history).mockResolvedValue({days:365,from:'2025-10-26',to:'2026-10-25',granularity:'daily_contract_aggregate',note:'Published daily aggregates, not individual executions.',coverage:{complete:false,retrieval_complete:true,available_days:252,unavailable_days:113,pending_days:0,first_available:'2025-10-27',last_available:'2026-10-23'},sync:{running:false,processed:365,total:365,error:null},insights:[],daily:[],rows:[],rows_total:0,offset:0,limit:50,expiries:[]});
 vi.mocked(healthApi.check).mockResolvedValue({status:'healthy',version:'0.1.0',database:'ok',timestamp:''});
 vi.mocked(monitorApi.status).mockResolvedValue({running:true,market_open:true,refresh_seconds:60,power:{supported:true,enabled:false},backoff_until:null,generated_at:"2026-10-26T12:00:00+05:30",last_sweep:null});
});
describe('365-day research and local controls',()=>{
 it('defaults to market history across every owned stock for 365 days',async()=>{
  renderWithRouter(<AnalyticsPage/>);
  expect(await screen.findByText('252 daily reports available')).toBeInTheDocument();
  expect(screen.getByRole('tab',{name:'Market history'})).toHaveAttribute('aria-selected','true');
  expect(screen.getByLabelText('Period')).toHaveValue('365');
  expect(screen.getByLabelText('Stock')).toHaveValue('');
  expect(screen.getByRole('option',{name:/^FULLYLENT/})).toBeInTheDocument();
  expect(screen.getByRole('link',{name:'Export all daily records'})).toHaveAttribute('href','/api/analytics/slb-trades.csv?days=365');
 });
 it('requests research for the selected held stock and window',async()=>{
  const user=userEvent.setup();renderWithRouter(<AnalyticsPage/>);
  await screen.findByRole('option',{name:/IRFC/});
  await user.selectOptions(screen.getByLabelText('Stock'),'IRFC');
  await user.selectOptions(screen.getByLabelText('Period'),'90');
  await waitFor(()=>expect(marketAnalyticsApi.history).toHaveBeenLastCalledWith('IRFC',90,1,'',expect.anything()));
 });
 it('persists the requested dark mode and calls the reversible awake API',async()=>{
  const user=userEvent.setup();renderWithRouter(<TopBar/>);
  await user.click(screen.getByRole('button',{name:'Switch to dark mode'}));
  expect(document.documentElement.dataset.theme).toBe('dark');
  expect(localStorage.getItem('slbm-theme')).toBe('dark');
  await user.click(await screen.findByRole('button',{name:'☕ Keep Mac awake'}));
  expect(monitorApi.awake).toHaveBeenCalledWith(true);
 });
});
