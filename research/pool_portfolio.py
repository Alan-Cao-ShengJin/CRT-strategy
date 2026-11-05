"""Selected pool versus fixed controls, with one shared account and common clock."""
from hashlib import sha256
from heapq import merge
from itertools import groupby
from pathlib import Path
import argparse
import gzip
import json

from crt.models import MINUTE
from crt.strategy import trading_day
from research.pool_markets import PROTOCOL, ready_path, register_pool
from research.pool_select import SELECTION, fingerprints
from research.portfolio_study import Portfolio, symbol_bars

RESULTS = Path('research/results/pool_portfolios')


class ClockPortfolio(Portfolio):
    def batch(self, bars, start, end):
        at = bars[0].start
        day = trading_day(at)
        super().batch(bars, start, end)
        if start <= day < end:
            if any(b.symbol == 'MNQ' for b in bars):
                self.market_days.add(day)
            self.daily[day] = self.equity
            self.last_at = at+MINUTE


def case_definition(name, initial):
    policy = json.loads(PROTOCOL.read_text())
    old = json.loads(Path('research/portfolio_protocol.json').read_text())
    selection = json.loads(SELECTION.read_text())
    assert selection['source_sha256'] == fingerprints()
    assert selection['policy_sha256'] == sha256(PROTOCOL.read_bytes()).hexdigest()
    late = name.startswith('later_')
    kind = name.removeprefix('later_').removeprefix('full_')
    baskets = dict(selected=selection['selected'], all17=old['baskets']['all_17_same_5m'],
                   earlier_five=old['baskets']['screened_five_retrospective'], mcl_only={'MCL': 5})
    return baskets[kind], policy['selection_cutoff_exclusive'] if late else policy['training_start'], policy['test_end_exclusive']


def run(name, initial, path):
    register_pool()
    RESULTS.mkdir(exist_ok=True)
    target = RESULTS/f'{name}_{initial}_{path}.json.gz'
    source = fingerprints()
    digest = sha256(PROTOCOL.read_bytes()).hexdigest()
    selection_hash = sha256(SELECTION.read_bytes()).hexdigest()
    if target.exists():
        with gzip.open(target, 'rt') as stream:
            old = json.load(stream)
        assert old['source_sha256'] == source and old['policy_sha256'] == digest and old['selection_sha256'] == selection_hash
        return
    basket, start, end = case_definition(name, initial)
    ready = {s: json.loads(ready_path(s).read_text()) for s in set(basket) | {'MNQ'}}
    inputs = {}
    for r in ready.values():
        inputs.update(r['input_sha256']);inputs[r['schedule_file']] = r['calendar_sha256']
    for file, expected in inputs.items():
        assert sha256(Path(file).read_bytes()).hexdigest() == expected
    capital = json.loads(Path('research/portfolio_protocol.json').read_text())
    costs = json.loads(Path('research/choch_breadth_protocol.json').read_text())
    p = ClockPortfolio(basket, initial, path, capital, costs, ready)
    for at, group in groupby(merge(*(symbol_bars(r) for r in ready.values()), key=lambda b: (b.start, b.symbol)), key=lambda b: b.start):
        if trading_day(at) >= end:
            break
        p.batch(list(group), start, end)
    p.finish()
    result = p.result()
    result.update(name=name, basket=basket, starting_equity=initial, path=path, start=start, end_exclusive=end,
                  source_sha256=source, policy_sha256=digest, selection_sha256=selection_hash, input_sha256=inputs,
                  capital_policy_sha256=sha256(Path('research/portfolio_protocol.json').read_bytes()).hexdigest())
    with target.open('wb') as raw:
        with gzip.GzipFile(fileobj=raw, mode='wb', mtime=0) as stream:
            stream.write(json.dumps(result, allow_nan=False).encode())
    print(json.dumps(dict(stage='portfolio_complete', name=name, initial=initial, path=path, stats=result['stats'])), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('name')
    parser.add_argument('--equity', type=int, default=50000)
    parser.add_argument('--path', choices=['high_first', 'low_first'], required=True)
    args = parser.parse_args()
    run(args.name, args.equity, args.path)
