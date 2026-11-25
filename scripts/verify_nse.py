"""Compare pre-existing holdings quotes with NSE's independent stock-page feed.

The existing /api/slb/quote route retrieves official GetQuote SLBM data using
its shared scheduler client/rate limiter. Capture the board BEFORE that call:
the route persists returned quotes, so comparing afterward would be circular.
Timestamp differences remain explicit; moving quotes are not called errors.
"""
import argparse
import datetime as dt
import json
import urllib.parse
import urllib.request
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
FIELDS = ('ltp', 'best_bid', 'best_bid_qty', 'best_ask', 'best_ask_qty',
          'traded_quantity', 'open_positions')


def get(path):
    with urllib.request.urlopen('http://127.0.0.1:8010' + path, timeout=45) as response:
        return json.load(response)


def timestamp(value):
    if not value:
        return None
    try:
        parsed = dt.datetime.strptime(value, '%d-%b-%Y %H:%M:%S').replace(tzinfo=IST)
    except ValueError:
        try:
            parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            return None
    return parsed.astimezone(dt.timezone.utc)


def compare(holding, official):
    result = {'symbol': holding['symbol'], 'official_source': official.get('source'),
              'official_as_of': official.get('as_of'), 'status': 'verified', 'rows': []}
    if official.get('blocked') or official.get('source') != 'quote':
        result['status'] = 'unverified_cached_or_blocked'
        return result
    board = {r['series_code']: r for r in holding['series'] if r['series_family'] == 'B'}
    rows = [r for r in official.get('series', []) if r['series_family'] == 'B']
    for row in rows:
        code = row['series_code']
        saved = board.get(code)
        if saved is None:
            result['rows'].append({'series_code': code, 'status': 'not_on_before_board',
                                   'official_active': row.get('is_active', False)})
            continue
        differences = {}
        if saved['settlement_date'] != row['settlement_date']:
            differences['settlement_date'] = {'app': saved['settlement_date'], 'nse': row['settlement_date']}
        for field in FIELDS:
            if Decimal(str(saved.get(field) or 0)) != Decimal(str(row.get(field) or 0)):
                differences[field] = {'app': saved.get(field), 'nse': row.get(field)}
        # Missing underlying in the SLB feed is intentional: the app uses the
        # separately timestamped NSE equity/ETF quote, never a fabricated zero.
        if saved.get('underlying_price') and row.get('underlying_price'):
            if Decimal(str(saved['underlying_price'])) != Decimal(str(row['underlying_price'])):
                differences['underlying_price'] = {'app': saved['underlying_price'], 'nse': row['underlying_price']}
        app_at, nse_at = timestamp(saved.get('updated_at')), timestamp(official.get('as_of'))
        aligned = bool(app_at and nse_at and app_at == nse_at)
        status = ('same_timestamp_difference' if aligned else 'different_observation_times') if differences else 'matched'
        if differences and saved.get('is_stale'):
            status = 'cached_app_observation'
        if 'settlement_date' in differences:
            status = 'settlement_difference'
        result['rows'].append({'series_code': code, 'settlement_date': row['settlement_date'],
                               'app_as_of': saved.get('updated_at'), 'app_is_stale': saved.get('is_stale',False), 'status': status,
                               'time_gap_seconds': abs((app_at-nse_at).total_seconds()) if app_at and nse_at else None,
                               'differences': differences})
    if not rows and not board:
        result['status'] = 'no_series_b_on_either_view'
    result['official_series_b'] = rows
    return result


def verify(symbols=None, limit=3):
    now = dt.datetime.now(IST)
    day = now.date().isoformat()
    scheduler = get('/api/scheduler/status')
    if scheduler.get('backoff_until') and timestamp(scheduler['backoff_until']) > now.astimezone(dt.timezone.utc):
        return {'at': now.isoformat(), 'status': 'skipped_nse_backoff', 'checks': []}
    runtime = ROOT/'runtime'
    runtime.mkdir(exist_ok=True)
    state_path = runtime/('nse-verification-state-'+day+'.json')
    try:
        state = json.loads(state_path.read_text())
    except (ValueError, OSError):
        state = {'cursor': 0, 'checked_symbols': []}
    initial = get('/api/portfolio/live')
    available = sorted(h['symbol'] for h in initial['holdings'])
    if symbols:
        selected = [s.strip().upper() for s in symbols if s.strip().upper() in available]
    else:
        selected = [available[(state['cursor']+i) % len(available)] for i in range(min(limit, len(available)))] if available else []
    checks = []
    for symbol in selected:
        # Re-read immediately before each independently fetched stock-page quote.
        before = get('/api/portfolio/live')
        holding = next(h for h in before['holdings'] if h['symbol'] == symbol)
        official = get('/api/slb/quote/'+urllib.parse.quote(symbol, safe=''))
        check = compare(holding, official)
        check['official_page'] = 'https://www.nseindia.com/get-quote/slb/'+symbol+'/'+symbol
        checks.append(check)
        if official.get('blocked'):
            break  # Do not try another symbol after the first NSE block.
        if official.get('source') == 'quote':
            state['checked_symbols'] = sorted(set(state['checked_symbols']) | {symbol})
    if not symbols and available:
        state['cursor'] = (state['cursor']+len(checks)) % len(available)
    result = {'at': now.isoformat(), 'status': 'completed', 'checks': checks,
              'held_symbols': len(available), 'distinct_symbols_verified_today': len(state['checked_symbols']),
              'note': 'Compared with the holdings board captured before the official NSE stock-page request. Different observation times can produce normal price/quantity changes. NSE gross annualised yield is not the app’s net yield after charges.'}
    with (runtime/('nse-verification-'+day+'.jsonl')).open('a') as output:
        output.write(json.dumps(result)+'\n')
    state_path.write_text(json.dumps(state, indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbols', nargs='*')
    parser.add_argument('--limit', type=int, default=3)
    args = parser.parse_args()
    output = verify(args.symbols, args.limit)
    summary = {k: v for k, v in output.items() if k not in ('checks', 'note')}
    summary['checks'] = [{'symbol': c['symbol'], 'status': c['status'],
                          'matched': sum(r['status'] == 'matched' for r in c['rows']),
                          'time_skewed_differences': sum(r['status'] == 'different_observation_times' for r in c['rows']),
                          'cached_app_differences': sum(r['status'] == 'cached_app_observation' for r in c['rows']),
                          'structural_or_aligned_differences': [r for r in c['rows'] if r['status'] in ('settlement_difference', 'same_timestamp_difference')]}
                         for c in output['checks']]
    print(json.dumps(summary, indent=2))
