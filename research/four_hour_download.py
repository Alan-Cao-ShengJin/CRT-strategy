"""Cached dated-contract minute acquisition for the frozen four-hour protocol."""
import calendar
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path

import numpy as np

from crt.massive import MassiveClient
from crt.models import NY, UTC

DEST = Path('data/choch_four_hour')
PROTOCOL = Path('research/four_hour_protocol.json')


def windows(root, start, end):
    begin = start
    for year in range(start.year, end.year+2):
        for month, code in ((3, 'H'), (6, 'M'), (9, 'U'), (12, 'Z')):
            fridays = [w[calendar.FRIDAY] for w in calendar.monthcalendar(year, month) if w[calendar.FRIDAY]]
            third = datetime(year, month, fridays[2], 16, tzinfo=NY)
            roll = (third-timedelta(days=8)).astimezone(UTC)
            if roll <= begin:
                continue
            until = min(roll, end)
            yield root+code+str(year % 10), begin, until
            begin = until
            if begin == end:
                return
    raise RuntimeError('Incomplete roll calendar')


def cached_pages(client, path, params, stem):
    visited = set()
    for n in range(1000):
        file = DEST/'raw'/f'{stem}_{n}.json'
        if file.exists():
            doc = json.loads(file.read_text())
        else:
            doc = client.request(path, params)
            file.write_text(json.dumps(doc), encoding='utf-8')
        yield from doc.get('results', [])
        path, params = doc.get('next_url'), None
        if not path:
            return
        if path in visited:
            raise RuntimeError('Repeated pagination cursor')
        visited.add(path)
    raise RuntimeError('Pagination safety limit')


def run():
    p = json.loads(PROTOCOL.read_text())
    start, end = (datetime.fromisoformat(p[k]) for k in ('data_start', 'end_exclusive'))
    DEST.mkdir(parents=True, exist_ok=True)
    (DEST/'raw').mkdir(exist_ok=True)
    client = MassiveClient()
    for root in p['markets']:
        schedule = DEST/f'schedules_{root}.json'
        if not schedule.exists():
            rows = list(cached_pages(client, '/futures/v1/schedules', {
                'product_code': root, 'session_end_date.gte': start.date().isoformat(),
                'session_end_date.lte': (end+timedelta(days=1)).date().isoformat(), 'limit': 1000}, f'calendar_{root}'))
            if not rows:
                raise RuntimeError(f'No calendar: {root}')
            schedule.write_text(json.dumps(rows), encoding='utf-8')
        records = []
        for ticker, begin, until in windows(root, start, end):
            file = DEST/f'{ticker}_{begin.date()}_{until.date()}.npz'
            # Include the old contract's roll-minute open for the simultaneous basis.
            stop = min(until+timedelta(minutes=1), end)
            if not file.exists():
                rows = list(cached_pages(client, '/futures/v1/aggs/'+ticker, {
                    'resolution': '1min', 'window_start.gte': str(int(begin.timestamp())*10**9),
                    'window_start.lt': str(int(stop.timestamp())*10**9),
                    'sort': 'window_start.asc', 'limit': 50000}, ticker+'_'+str(begin.date())+'_'+str(until.date())))
                unique, conflicts = {}, set()
                for r in rows:
                    t = int(r['window_start'])//10**9
                    if begin.timestamp() <= t < stop.timestamp():
                        v = tuple(float(r[k]) for k in ('open', 'high', 'low', 'close'))
                        if t in unique and unique[t] != v:
                            conflicts.add(t)
                        unique[t] = v
                if conflicts:
                    # Neither duplicate is authoritative. Quarantine the minute,
                    # let candle coverage reject it, and preserve the raw evidence.
                    for t in conflicts:
                        unique.pop(t)
                    file.with_suffix('.conflicts.json').write_text(json.dumps(sorted(conflicts)), encoding='utf-8')
                if not unique:
                    raise RuntimeError(f'Empty prices: {ticker}')
                ts = sorted(unique)
                np.savez_compressed(file, t=np.array(ts, dtype=np.int64), p=np.array([unique[t] for t in ts]))
            with np.load(file) as d:
                count = len(d['t'])
            conflict_file = file.with_suffix('.conflicts.json')
            records.append(dict(contract=ticker, start=begin.isoformat(), end=until.isoformat(),
                                file=str(file), sha256=sha256(file.read_bytes()).hexdigest(), rows=count,
                                quarantined_minutes=json.loads(conflict_file.read_text()) if conflict_file.exists() else []))
            print(json.dumps(dict(root=root, contract=ticker, rows=count, stage='contract_ready')), flush=True)
        ready = dict(root=root, records=records, schedule_file=str(schedule),
                     calendar_sha256=sha256(schedule.read_bytes()).hexdigest(),
                     protocol_sha256=sha256(PROTOCOL.read_bytes()).hexdigest())
        (DEST/f'READY_{root}.json').write_text(json.dumps(ready, indent=2), encoding='utf-8')
        print(json.dumps(dict(root=root, stage='ready')), flush=True)


if __name__ == '__main__':
    run()
