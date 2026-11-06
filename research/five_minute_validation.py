"""Frozen five-minute validation on earlier history and feasible account sizes."""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from hashlib import sha256
from heapq import merge
from itertools import groupby
import gzip
import json
from pathlib import Path
from types import SimpleNamespace
import time

from crt.strategy import trading_day
from research.breadth_study import source_hashes, stressed_pnl
from research.expanded_markets import register, market
from research.five_minute_download import DEST, PROTOCOL
from research.pool_portfolio import ClockPortfolio
from research.portfolio_study import symbol_bars
from research.universe65_fast import FastClockPortfolio

OUT = Path('research/results/five_minute_validation')


def read(file):
    with gzip.open(file, 'rt', encoding='utf-8') as stream:
        return json.load(stream)


def write(file, doc):
    file.parent.mkdir(parents=True, exist_ok=True)
    with file.open('wb') as raw:
        with gzip.GzipFile(fileobj=raw, mode='wb', mtime=0) as stream:
            stream.write(json.dumps(doc, allow_nan=False).encode())


def fingerprints():
    files = [Path(__file__), Path('research/five_minute_download.py'), Path('research/portfolio_study.py'),
             Path('research/pool_portfolio.py'), Path('research/universe65_fast.py')]
    return source_hashes() | {str(p): sha256(p.read_bytes()).hexdigest() for p in files}


def cases(policy):
    result = []
    for period, baskets, capitals, start, end, stress in [
        ('earlier', policy['baskets'], policy['earlier_capitals'], policy['earlier_start'], policy['previous_start'], False),
        ('extended', policy['baskets'], [50000], policy['earlier_start'], policy['end_exclusive'], False),
        ('control', {s: {s: 5} for s in policy['symbols']}, [50000], '2026-05-01', policy['end_exclusive'], False),
        ('earlier', policy['baskets'], [50000], policy['earlier_start'], policy['previous_start'], True)]:
        for name, basket in baskets.items():
            for capital in capitals:
                for path in policy['paths']:
                    result.append(dict(period=period, name=name, basket=basket, capital=capital,
                                       start=start, end=end, path=path, stress=stress,
                                       tag=f'{period}_{name}_{capital}_{path}_{"stress" if stress else "base"}'))
    return result


def play(cls, case, ready, capital_policy, costs):
    engine = cls(case['basket'], case['capital'], case['path'], capital_policy, costs, ready)
    if case['stress']:
        for e in engine.engines.values():
            e.config.slippage_ticks = 2
            e.config.commission_per_side = {s: 1.50 for s in e.config.commission_per_side}
    streams = [symbol_bars(r) for r in ready.values()]
    for at, group in groupby(merge(*streams, key=lambda b: (b.start, b.symbol)), key=lambda b: b.start):
        if trading_day(at) >= case['end']:
            break
        engine.batch(list(group), case['start'], case['end'])
    engine.finish()
    result = engine.result()
    result['setups'] = {s: {t['setup_id']: engine.engines[s].setup_details[t['setup_id']]
                           for t in result['trades'] if t['symbol'] == s} for s in case['basket']}
    return result


def run(case):
    register()
    OUT.mkdir(parents=True, exist_ok=True)
    file = OUT/(case['tag']+'.json.gz')
    protocol_sha = sha256(PROTOCOL.read_bytes()).hexdigest()
    code = fingerprints()
    if file.exists():
        previous = read(file)
        assert previous['protocol_sha256'] == protocol_sha and previous['source_sha256'] == code
        return case['tag']
    policy = json.loads(PROTOCOL.read_text())
    ready = {s: json.loads((Path(f'data/choch_expanded/READY_{s}.json') if case['period'] == 'control'
                           else DEST/f'READY_{s}.json').read_text()) for s in set(case['basket']) | {'MNQ'}}
    inputs = {}
    for doc in ready.values():
        inputs.update(doc['input_sha256']); inputs[doc['schedule_file']] = doc['calendar_sha256']
    for f, digest in inputs.items():
        assert sha256(Path(f).read_bytes()).hexdigest() == digest, 'Changed input'
    capital_policy = json.loads(Path('research/portfolio_protocol.json').read_text())
    costs = json.loads(Path('research/choch_breadth_protocol.json').read_text())
    begin = time.monotonic()
    result = play(FastClockPortfolio, case, ready, capital_policy, costs)
    if case['period'] == 'control':
        if case['name'] == 'MCL':
            reference = Path('research/results/pool_portfolios')/f'later_mcl_only_50000_{case["path"]}.json.gz'
            old = read(reference)
            control = dict(reference=str(reference), sha256=sha256(reference.read_bytes()).hexdigest())
        else:
            old = play(ClockPortfolio, case, ready, capital_policy, costs)
            control = dict(reference='fresh original ClockPortfolio replay')
        assert result['trades'] == old['trades'] and result['daily'] == old['daily'], 'Control mismatch'
        result['control'] = dict(status='passed', **control)
    ts = result['trades']
    result['stats'].update(net_r=sum(t['net']/t['risk'] for t in ts),
                           without_best_five=sum(t['net'] for t in ts)-sum(sorted((t['net'] for t in ts), reverse=True)[:5]),
                           fixed_fill_stress_net_pnl=sum(stressed_pnl([SimpleNamespace(**t)], market(t['symbol']), costs) for t in ts) if not case['stress'] else None)
    # Allocation records carry the actual shared-equity denominator; single-account
    # sequential realized cash cannot substitute when two positions overlap.
    result.update(case=case, source_sha256=code, protocol_sha256=protocol_sha,
                  input_sha256=inputs, seconds=time.monotonic()-begin,
                  capital_policy=capital_policy, cost_policy=costs)
    write(file, result)
    print(json.dumps(dict(stage='complete', tag=case['tag'], trades=len(ts),
                         return_pct=result['stats']['return_pct'], seconds=result['seconds'])), flush=True)
    return case['tag']


def main(workers, periods=None):
    policy = json.loads(PROTOCOL.read_text())
    todo = [c for c in cases(policy) if periods is None or c['period'] in periods]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run, c): c['tag'] for c in todo}
        for future in as_completed(futures):
            future.result()


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workers', type=int, default=3)
    p.add_argument('--periods', nargs='+', choices=['earlier', 'extended', 'control'])
    args = p.parse_args()
    main(args.workers, args.periods)
