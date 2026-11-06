"""Earlier dated micro-futures data for the fixed-rule five-minute validation."""
from datetime import datetime, timedelta, date
from hashlib import sha256
import json
from pathlib import Path
import re

from crt.data import write_bars
from crt.massive import MassiveClient
from crt.models import Bar, NY, UTC
from research.expanded_markets import register, market
from research.download_annual import roll_schedule

DEST = Path('data/choch_five_minute_validation')
PROTOCOL = Path('research/five_minute_validation_protocol.json')


def pages(client, path, params, stem):
    visited = set()
    for n in range(1000):
        f = DEST/'raw'/f'{stem}_{n}.json'
        if f.exists():
            doc = json.loads(f.read_text())
        else:
            doc = client.request(path, params)
            f.write_text(json.dumps(doc), encoding='utf-8')
        yield from doc.get('results', [])
        path, params = doc.get('next_url'), None
        if not path:
            return
        if path in visited:
            raise RuntimeError('Repeated cursor')
        visited.add(path)
    raise RuntimeError('Pagination limit')


def windows(root, start, end, definitions):
    begin = start
    for row in sorted(definitions, key=lambda r: r['last_trade_date']):
        ticker = row['ticker']
        if not re.fullmatch(root+r'[FGHJKMNQUVXZ]5', ticker):
            continue
        if root == 'MNQ':
            rolls = {t: at for at, t in roll_schedule(root)}
            if ticker not in rolls:
                continue
            roll = rolls[ticker]
        else:
            d = date.fromisoformat(row['last_trade_date'])-timedelta(days=7)
            roll = datetime(d.year, d.month, d.day, 18, tzinfo=NY).astimezone(UTC)
        if roll <= begin:
            continue
        if not row['first_trade_date'] <= begin.date().isoformat() <= row['last_trade_date']:
            continue
        if abs(row['trade_tick_size']-market(root).tick) > 1e-8:
            raise RuntimeError('Tick mismatch')
        until = min(roll, end)
        yield ticker, begin, until, row
        begin = until
        if begin == end:
            return
    raise RuntimeError(f'Incomplete historical references for {root}')


def main():
    register(); client = MassiveClient()
    DEST.mkdir(parents=True, exist_ok=True); (DEST/'raw').mkdir(exist_ok=True)
    policy = json.loads(PROTOCOL.read_text())
    start = datetime.fromisoformat(policy['data_start'])
    # Reuse the exact first old segment after its boundary; no overlapping bars.
    for root in policy['symbols']:
        old = json.loads(Path(f'data/choch_expanded/READY_{root}.json').read_text())
        end = datetime.fromisoformat(old['records'][0]['start'])
        # Query enough listed contracts at the initial date and again in autumn.
        # Do not require modern 'single' type for older reference snapshots.
        definitions = {}
        for at in ('2025-03-17', '2025-08-01'):
            for row in pages(client, '/futures/v1/contracts', {'product_code': root, 'date': at, 'limit': 1000}, root+'_definitions_'+at):
                if re.fullmatch(root+r'[FGHJKMNQUVXZ]5', row.get('ticker', '')):
                    definitions[row['ticker']] = row
        schedule = DEST/f'schedules_{root}.json'
        if not schedule.exists():
            earlier = list(pages(client, '/futures/v1/schedules', {'product_code': root,
                'session_end_date.gte': '2025-03-17', 'session_end_date.lte': '2025-11-03', 'limit': 1000}, root+'_calendar'))
            if not earlier:
                raise RuntimeError('No earlier calendar')
            schedule.write_text(json.dumps(earlier+json.loads(Path(old['schedule_file']).read_text())), encoding='utf-8')
        records = []
        for ticker, begin, until, definition in windows(root, start, end, definitions.values()):
            file = DEST/f'{ticker}_{begin.date()}_{until.date()}.csv'
            if not file.exists():
                prices, conflicts = {}, set()
                for row in pages(client, '/futures/v1/aggs/'+ticker, {'resolution': '1min',
                    'window_start.gte': str(int(begin.timestamp())*10**9),
                    'window_start.lt': str(int(until.timestamp())*10**9),
                    'sort': 'window_start.asc', 'limit': 50000}, ticker+'_'+str(begin.date())+'_'+str(until.date())):
                    at = datetime.fromtimestamp(int(row['window_start'])//10**9, UTC)
                    if begin <= at < until:
                        bar = Bar(root, at, *(float(row[k]) for k in ('open', 'high', 'low', 'close')),
                                  float(row.get('volume', 0)), ticker)
                        if at in prices and prices[at] != bar:
                            conflicts.add(at)
                        prices[at] = bar
                for at in conflicts:
                    prices.pop(at)
                if not prices:
                    raise RuntimeError('Empty prices: '+ticker)
                write_bars(file, sorted(prices.values(), key=lambda b: b.start))
                file.with_suffix('.conflicts.json').write_text(json.dumps([at.isoformat() for at in sorted(conflicts)]))
            records.append(dict(root=root, contract=ticker, start=begin.isoformat(), end=until.isoformat(), file=str(file), definition=definition))
            print(json.dumps(dict(stage='earlier_contract_ready', root=root, contract=ticker)), flush=True)
        records.extend(old['records'])
        inputs = {r['file']: sha256(Path(r['file']).read_bytes()).hexdigest() for r in records}
        ready = dict(symbol=root, records=records, schedule_file=str(schedule), input_sha256=inputs,
                     calendar_sha256=sha256(schedule.read_bytes()).hexdigest(),
                     original_ready=old, protocol_sha256=sha256(PROTOCOL.read_bytes()).hexdigest())
        (DEST/f'READY_{root}.json').write_text(json.dumps(ready, indent=2), encoding='utf-8')
        print(json.dumps(dict(stage='ready', root=root, segments=len(records))), flush=True)


if __name__ == '__main__':
    main()
