"""Resumable data-only acquisition for the declared CHoCH breadth study."""
import argparse
import calendar
from datetime import date, datetime, timedelta
from hashlib import sha256
import importlib.metadata
import json
from math import ceil, floor
from pathlib import Path
import re

from crt.data import write_bars
from crt.massive import MassiveClient
from crt.models import Bar, NY, UTC, MINUTE
from research.download_annual import roll_schedule
from research.expanded_markets import EXTRA, market, register

DEST = Path('data/choch_expanded')
START = datetime(2025, 10, 27, tzinfo=UTC)
END = datetime(2026, 11, 5, tzinfo=UTC)


class Acquisition:
    def __init__(self):
        register()
        DEST.mkdir(parents=True, exist_ok=True)
        (DEST/'raw').mkdir(exist_ok=True)
        self.client = MassiveClient()
        self.definitions = {}
        for file in DEST.glob('*definition*.json'):
            doc = json.loads(file.read_text())
            for row in doc.get('results', []):
                self.definitions[row['ticker']] = row
        seed = DEST/'contract_specs.json'
        if seed.exists():
            for doc in json.loads(seed.read_text()).values():
                for row in doc.get('results', []):
                    self.definitions[row['ticker']] = row

    def pages(self, path, params, stem):
        visited = set()
        for page in range(1000):
            file = DEST/'raw'/f'{stem}_{page}.json'
            if file.exists():
                doc = json.loads(file.read_text())
            else:
                doc = self.client.request(path, params)
                file.write_text(json.dumps(doc), encoding='utf-8')
            rows = doc.get('results') or []
            yield rows, doc
            path, params = doc.get('next_url'), None
            if not path:
                return
            if path in visited:
                raise RuntimeError('Repeated data cursor')
            visited.add(path)
        raise RuntimeError('Data pagination safety limit')

    def definition(self, root, ticker, at):
        row = self.definitions.get(ticker)
        if row is None or not row.get('first_trade_date', '9999') <= at.date().isoformat() <= row.get('last_trade_date', ''):
            stem = f'definitions_{root}_{at.date()}'
            for rows, _ in self.pages('/futures/v1/contracts',
                                      {'product_code': root, 'date': at.date().isoformat(), 'type': 'single', 'limit': 1000}, stem):
                for r in rows:
                    self.definitions[r['ticker']] = r
            row = self.definitions.get(ticker)
        if row is None or not row['first_trade_date'] <= at.date().isoformat() <= row['last_trade_date']:
            raise RuntimeError(f'No valid historical definition for {ticker} at {at.date()}')
        if abs(row['trade_tick_size']-market(root).tick) > 1e-9:
            raise RuntimeError(f'Trade tick mismatch for {ticker}')
        if not re.fullmatch(re.escape(root)+r'[FGHJKMNQUVXZ]\d{1,4}', ticker):
            raise RuntimeError('Only dated outright contracts are allowed')
        return row

    def windows(self, root):
        begin = START
        if root in ('MYM', 'M2K'):
            schedule = roll_schedule(root)
        elif root == 'SIL':
            schedule = []
            for year in range(2025, 2028):
                for month in (3, 5, 7, 9, 12):
                    d = date(year, month, 1)-timedelta(days=7)
                    schedule.append((datetime(d.year, d.month, d.day, 18, tzinfo=NY).astimezone(UTC),
                                     root+'FGHJKMNQUVXZ'[month-1]+str(year % 10)))
        else:
            schedule = []
            months = [(2025, 12)]+[(2026, m) for m in (range(1, 13) if root == 'MCL' else (3, 6, 9, 12))]
            for year, month in months:
                ticker = root+'FGHJKMNQUVXZ'[month-1]+str(year % 10)
                row = self.definition(root, ticker, begin)
                d = date.fromisoformat(row['last_trade_date'])-timedelta(days=7)
                roll = datetime(d.year, d.month, d.day, 18, tzinfo=NY).astimezone(UTC)
                until = min(roll, END)
                if until <= begin:
                    raise RuntimeError(f'Invalid roll ordering for {ticker}')
                yield ticker, begin, until, row
                begin = until
                if begin >= END:
                    return
            raise RuntimeError(f'Incomplete roll schedule for {root}')
        for roll, ticker in schedule:
            if roll <= begin:
                continue
            row = self.definition(root, ticker, begin)
            until = min(roll, END)
            if until.date().isoformat() > row['last_trade_date']:
                raise RuntimeError(f'Roll past expiration: {ticker}')
            yield ticker, begin, until, row
            begin = until
            if begin >= END:
                return

    def future_bars(self, root, ticker, begin, end):
        ns = lambda x: str(int(x.timestamp())*1000000000)
        stem = f'aggs_{ticker}_{begin.date()}_{end.date()}'
        bars = {}
        for rows, _ in self.pages('/futures/v1/aggs/'+ticker,
                                  {'resolution': '1min', 'window_start.gte': ns(begin), 'window_start.lt': ns(end),
                                   'sort': 'window_start.asc', 'limit': 50000}, stem):
            for r in rows:
                at = datetime.fromtimestamp(int(r['window_start'])//1000000000, UTC)
                if begin <= at < end:
                    bars[at] = Bar(root, at, *(float(r[k]) for k in ('open', 'high', 'low', 'close')),
                                   float(r.get('volume', 0)), ticker)
            print(json.dumps(dict(stage='future_page', symbol=root, ticker=ticker, collected=len(bars))), flush=True)
        return sorted(bars.values(), key=lambda b: b.start)

    def schedules(self, root):
        path = DEST/f'schedules_{root}.json'
        if not path.exists():
            rows = list(self.client.pages('/futures/v1/schedules', {'product_code': root,
                        'session_end_date.gte': '2025-10-26', 'session_end_date.lte': '2026-11-06', 'limit': 1000}))
            if not rows:
                raise RuntimeError(f'No exchange calendar for {root}')
            path.write_text(json.dumps(rows, indent=2), encoding='utf-8')
        return path

    def stock_calendar(self):
        import exchange_calendars as xcals
        path = DEST/'stock_sessions.json'
        if not path.exists():
            cal = xcals.get_calendar('XNYS', start='2025-10-20', end='2026-11-10')
            sessions = cal.schedule.loc['2025-10-27':'2026-11-06']
            rows = []
            for symbol, m in EXTRA.items():
                if m.kind != 'stock':
                    continue
                for _, row in sessions.iterrows():
                    for field, event in (('open', 'open'), ('close', 'close')):
                        rows.append(dict(product_code=symbol, event=event, timestamp=row[field].isoformat()))
            path.write_text(json.dumps(rows, indent=2), encoding='utf-8')
        return path

    def spot_bars(self, symbol, calendar_path):
        from crt.calendar import load_sessions
        m = market(symbol)
        intervals = load_sessions(calendar_path, [symbol])[symbol]
        regular = {a.astimezone(NY).date().isoformat(): (a, b) for a, b in intervals}
        begin_ms, end_ms = int(START.timestamp())*1000, int(END.timestamp())*1000
        bars, normalized = {}, 0
        cursor = begin_ms
        while cursor < end_ms:
            stem = f'spot_{symbol}_{cursor}_{end_ms}'
            last, count = None, 0
            for rows, doc in self.pages(f'/v2/aggs/ticker/{m.api_symbol}/range/1/minute/{cursor}/{end_ms-1}',
                                        {'adjusted': 'true', 'sort': 'asc', 'limit': 50000}, stem):
                for r in rows:
                    millis = int(r['t']);last = max(last or millis, millis);count += 1
                    at = datetime.fromtimestamp(millis/1000, UTC)
                    if not START <= at < END:
                        continue
                    if m.kind == 'stock':
                        interval = regular.get(at.astimezone(NY).date().isoformat())
                        if interval is None or not interval[0] <= at < interval[1]:
                            continue
                    raw = [float(r[k]) for k in ('o', 'h', 'l', 'c')]
                    prices = [round(round(raw[0]/.01)*.01, 8), round(ceil(raw[1]/.01-1e-8)*.01, 8),
                              round(floor(raw[2]/.01+1e-8)*.01, 8), round(round(raw[3]/.01)*.01, 8)]
                    normalized += int(any(abs(a-b) > 1e-8 for a, b in zip(raw, prices)))
                    bars[at] = Bar(symbol, at, *prices, float(r.get('v', 0)), symbol)
                print(json.dumps(dict(stage='spot_page', symbol=symbol, collected=len(bars), raw_rows=count)), flush=True)
            if not count or last is None or last >= end_ms-60000:
                break
            # Some aggregate responses stop at the base-bar limit without a
            # cursor. Continue after the last returned minute, never fabricate.
            if last+60000 <= cursor:
                raise RuntimeError('Aggregate download did not advance')
            cursor = last+60000
        return sorted(bars.values(), key=lambda b: b.start), normalized

    def run(self, symbol):
        ready = DEST/f'READY_{symbol}.json'
        if ready.exists():
            print(json.dumps(dict(stage='ready_cached', symbol=symbol)), flush=True)
            return
        if symbol not in EXTRA:
            records = [r for r in json.loads(Path('data/annual/manifest.json').read_text()) if r['root'] == symbol]
            calendar_path = Path('data/annual/schedules.json')
        elif market(symbol).kind == 'futures':
            records = []
            for ticker, begin, end, definition in self.windows(symbol):
                path = DEST/f'{ticker}_{begin.date()}_{end.date()}.csv'
                if not path.exists():
                    bars = self.future_bars(symbol, ticker, begin, end)
                    if not bars:
                        raise RuntimeError(f'No prices for declared contract {ticker}')
                    write_bars(path, bars)
                records.append(dict(root=symbol, contract=ticker, start=begin.isoformat(), end=end.isoformat(),
                                    file=str(path), definition=definition))
                (DEST/f'manifest_{symbol}.json').write_text(json.dumps(records, indent=2), encoding='utf-8')
                print(json.dumps(dict(stage='contract_complete', symbol=symbol, ticker=ticker)), flush=True)
            calendar_path = self.schedules(symbol)
        else:
            if market(symbol).kind == 'stock':
                calendar_path = self.stock_calendar()
            else:
                calendar_path = DEST/'crypto_sessions.json'
                if not calendar_path.exists():
                    calendar_path.write_text(json.dumps([
                        dict(product_code=symbol, event='open', timestamp=START.isoformat()),
                        dict(product_code=symbol, event='close', timestamp=(END+timedelta(days=1)).isoformat())]), encoding='utf-8')
            path = DEST/f'{symbol}_{START.date()}_{END.date()}.csv'
            normalization = 0
            if not path.exists():
                bars, normalization = self.spot_bars(symbol, calendar_path)
                if not bars or bars[-1].start < END-timedelta(days=5):
                    raise RuntimeError(f'Insufficient tail coverage: {symbol}')
                write_bars(path, bars)
            records = [dict(root=symbol, contract=symbol, start=START.isoformat(), end=END.isoformat(),
                            file=str(path), normalized_bars=normalization)]
        payload = dict(symbol=symbol, records=records, schedule_file=str(calendar_path),
                       calendar_sha256=sha256(calendar_path.read_bytes()).hexdigest(),
                       input_sha256={r['file']: sha256(Path(r['file']).read_bytes()).hexdigest() for r in records})
        if market(symbol).kind == 'stock':
            payload['exchange_calendars_version'] = importlib.metadata.version('exchange_calendars')
        ready.write_text(json.dumps(payload, indent=2), encoding='utf-8')
        print(json.dumps(dict(stage='ready', symbol=symbol, segments=len(records))), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--symbols', nargs='+')
    args = p.parse_args()
    wanted = args.symbols or json.loads(Path('research/choch_breadth_protocol.json').read_text())['symbols']
    acquisition = Acquisition()
    for symbol in wanted:
        acquisition.run(symbol)
