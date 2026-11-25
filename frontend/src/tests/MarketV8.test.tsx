import { beforeEach, describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { LiveSeries } from '@/types';
import { LiveBoardPage } from '@/pages/LiveBoardPage';
import { renderWithRouter } from './testUtils';
vi.mock('@/api/live',()=>({liveApi:{board:vi.fn(),trades:vi.fn()}}));
import { liveApi } from '@/api/live';
const q=(code:string,month:string):LiveSeries=>({series_code:code,series_family:'B',series:month,settlement_date:code==='XD'?'2026-12-01':'2026-11-03',settlement_estimated:false,ltp:1,traded_quantity:100,open_positions:500,best_bid:1,best_bid_qty:100,best_ask:2,best_ask_qty:100,annualized_yield:5,net_yield_pa:4,net_fee:60,underlying_price:100,updated_at:'2026-10-26T05:00:00Z',age_seconds:10});
beforeEach(()=>{
 vi.mocked(liveApi.board).mockResolvedValue({generated_at:'2026-10-26T05:00:00Z',market_open:true,refresh_seconds:60,series_family:'B',last_sweep:null,sweep_warning:null,holdings:[
 {stock_id:1,symbol:'IRFC',name:'Indian Railway Finance Corporation',total_owned:100,in_demat:100,lent:0,status:'live',slb_checked_at:null,series:[q('XD','Dec-2026'),q('XN','Nov-2026')]},
 {stock_id:2,symbol:'IREDA',name:'Indian Renewable Energy',total_owned:50,in_demat:50,lent:0,status:'live',slb_checked_at:null,series:[q('XD','Dec-2026')]}]});
 vi.mocked(liveApi.trades).mockResolvedValue({since:'2026-10-26T00:00:00Z',inferred:true,events:[]});
});
describe('Holdings market',()=>{
 it('starts with all stocks and all expiries, with separate prices',async()=>{
  renderWithRouter(<LiveBoardPage/>);
  expect(await screen.findByTestId('row-IRFC-XD')).toBeInTheDocument();
  expect(screen.getByTestId('row-IRFC-XN')).toBeInTheDocument();
  expect(screen.getByTestId('row-IREDA-XD')).toBeInTheDocument();
  expect(screen.getByLabelText('Expiry')).toHaveValue('');
  expect(screen.getByText('Stock LTP')).toBeInTheDocument();
  expect(screen.getByText('Fee LTP')).toBeInTheDocument();
 });
 it('searches company names and filters by NSE expiry code',async()=>{
  const user=userEvent.setup();renderWithRouter(<LiveBoardPage/>);
  await screen.findByTestId('row-IRFC-XD');
  await user.type(screen.getByLabelText('Search holdings'),'railway');
  expect(screen.queryByTestId('row-IREDA-XD')).not.toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText('Expiry'),'XD');
  expect(screen.getByTestId('row-IRFC-XD')).toBeInTheDocument();
  expect(screen.queryByTestId('row-IRFC-XN')).not.toBeInTheDocument();
 });
});

it('labels a retained fee separately from today’s trading and borrower bid',async()=>{
 const board=await liveApi.board();
 const quote={...q('XD','Dec-2026'),traded_quantity:0,fee_from_previous_observation:true,fee_observed_at:'2026-10-26T05:00:00Z'};
 vi.mocked(liveApi.board).mockResolvedValue({...board,holdings:[{...board.holdings[0],series:[quote]}]});
 renderWithRouter(<LiveBoardPage/>);
 const row=await screen.findByTestId('row-IRFC-XD');
 expect(row).toHaveTextContent('Last reported fee');
 expect(row).not.toHaveTextContent('Traded today');
 expect(row.querySelector('[title^="Fee last reported"]')).toHaveAttribute('title',expect.stringContaining('26 Oct'));
});
