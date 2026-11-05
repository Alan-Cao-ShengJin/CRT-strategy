"""Keep both path journals for each declared candidate; existing cases are reused."""
from hashlib import sha256
from pathlib import Path
import argparse
import gzip
import json

from crt.data import read_bars
from research.breadth_study import collect, config
from research.choch_study import replay
from research.expanded_markets import ExpandedEngine
from research.pool_markets import PROTOCOL, ready_path, register_pool
from research.pool_select import SCREEN, fingerprints


def screen(symbol):
    register_pool()
    policy = json.loads(PROTOCOL.read_text())
    rules = json.loads(Path('research/choch_breadth_protocol.json').read_text())
    SCREEN.mkdir(exist_ok=True)
    target = SCREEN/f'{symbol}.json.gz'
    if target.exists():
        with gzip.open(target, 'rt') as stream:
            old = json.load(stream)
        assert old['policy_sha256'] == sha256(PROTOCOL.read_bytes()).hexdigest() and old['source_sha256'] == fingerprints()
        return
    ready = json.loads(ready_path(symbol).read_text())
    inputs = ready['input_sha256'] | {ready['schedule_file']: ready['calendar_sha256']}
    for file, expected in inputs.items():
        assert sha256(Path(file).read_bytes()).hexdigest() == expected
    if symbol not in policy['added_symbols']:
        with gzip.open(Path('research/results/choch_breadth_trades')/f'{symbol}.jsonl.gz', 'rt') as stream:
            cases = [r for r in map(json.loads, stream) if r['timeframe'] == policy['timeframe'] and r['entry_mode'] == policy['entry_mode']]
    else:
        bars = read_bars([r['file'] for r in ready['records']])
        cases = []
        for path in policy['paths']:
            cfg = config(symbol, path, rules, ready['schedule_file'])
            engine = ExpandedEngine(cfg, policy['timeframe'], policy['entry_mode'], policy['reward_risk'], rules)
            replay(engine, bars, rules['start_trading_date'], rules['end_exclusive_trading_date'])
            _, full = collect(engine, rules, symbol, policy['timeframe'], policy['entry_mode'], path)
            cases.append(full)
    assert len(cases) == 2
    record = dict(symbol=symbol, cases=cases, source_sha256=fingerprints(), input_sha256=inputs,
                  policy_sha256=sha256(PROTOCOL.read_bytes()).hexdigest())
    with target.open('wb') as raw:
        with gzip.GzipFile(fileobj=raw, mode='wb', mtime=0) as stream:
            stream.write(json.dumps(record, allow_nan=False).encode())
    print(json.dumps(dict(stage='candidate_complete', symbol=symbol, paths=len(cases))), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('symbol')
    screen(parser.parse_args().symbol)
