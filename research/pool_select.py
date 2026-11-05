"""Training-only selection; later trade outcomes never enter membership scores."""
from collections import Counter
from hashlib import sha256
from math import sqrt
from pathlib import Path
from types import SimpleNamespace
import gzip
import json

from crt.models import timestamp
from crt.strategy import trading_day
from research.breadth_study import source_hashes, stressed_pnl
from research.expanded_markets import market
from research.pool_markets import PROTOCOL, universe

SCREEN = Path('research/results/pool_candidates')
SELECTION = Path('research/results/pool_selection.json')


def fingerprints():
    names = ('portfolio_study.py', 'pool_markets.py', 'pool_select.py', 'pool_screen.py', 'pool_portfolio.py')
    return source_hashes() | {str(Path('research')/name): sha256((Path('research')/name).read_bytes()).hexdigest() for name in names}


def training_stats(symbol, trades, policy, costs):
    included = [t for t in trades if policy['training_start'] <= trading_day(timestamp(t['entry_time'])) < policy['selection_cutoff_exclusive']
                and trading_day(timestamp(t['exit_time'])) < policy['selection_cutoff_exclusive']
                and t['reason'] not in ('roll_data_end_mark', 'end_of_data')]
    gain = sum(max(t['net'], 0) for t in included)
    loss = -sum(min(t['net'], 0) for t in included)
    stress = [stressed_pnl([SimpleNamespace(**t)], market(symbol), costs) for t in included]
    blocks = [sum(t['net'] for t in included if start <= trading_day(timestamp(t['entry_time'])) < end)
              for start, end in policy['training_blocks']]
    pf = gain/loss if loss else None
    reasons = []
    if len(included) < policy['minimum_training_trades_each_path']:
        reasons.append('insufficient_training_trades')
    if (pf if pf is not None else float('inf') if gain > 0 else 0) < policy['minimum_training_profit_factor_each_path']:
        reasons.append('training_profit_factor')
    if sum(v > 0 for v in blocks) < policy['minimum_positive_training_blocks_each_path']:
        reasons.append('training_time_blocks')
    if sum(stress) <= 0:
        reasons.append('training_cost_stress')
    score = sum(v/t['risk'] for v, t in zip(stress, included))/sqrt(len(included)) if included else 0.
    return dict(trades=len(included), profit_factor=pf, net_pnl=sum(t['net'] for t in included),
                stressed_pnl=sum(stress), positive_blocks=sum(v > 0 for v in blocks), blocks=blocks,
                score=score, eligible=not reasons, failed_conditions=reasons)


def choose(candidates, policy):
    ranked = sorted((c for c in candidates if c['eligible']), key=lambda c: (-c['score'], c['symbol']))
    used, selected = Counter(), []
    for candidate in ranked:
        bucket = policy['exposure_buckets'][candidate['symbol']]
        if used[bucket] >= policy['maximum_per_exposure_bucket']:
            continue
        selected.append(candidate['symbol']);used[bucket] += 1
        if len(selected) == policy['maximum_selected_markets']:
            break
    return selected


def select_pool(unavailable=None):
    unavailable = unavailable or {}
    policy = json.loads(PROTOCOL.read_text())
    costs = json.loads(Path('research/choch_breadth_protocol.json').read_text())
    candidates, journal_hashes = [], {}
    for symbol in universe():
        if symbol in unavailable:
            candidates.append(dict(symbol=symbol, eligible=False, unavailable=unavailable[symbol], score=0., paths={}))
            continue
        path = SCREEN/f'{symbol}.json.gz'
        with gzip.open(path, 'rt') as stream:
            doc = json.load(stream)
        assert doc['source_sha256'] == fingerprints()
        assert doc['policy_sha256'] == sha256(PROTOCOL.read_bytes()).hexdigest()
        assert {r['path'] for r in doc['cases']} == set(policy['paths'])
        bypath = {r['path']: training_stats(symbol, r['trades'], policy, costs) for r in doc['cases']}
        candidate = dict(symbol=symbol, bucket=policy['exposure_buckets'][symbol], paths=bypath,
                         eligible=all(r['eligible'] for r in bypath.values()), score=min(r['score'] for r in bypath.values()))
        candidates.append(candidate)
        journal_hashes[str(path)] = sha256(path.read_bytes()).hexdigest()
    selected = choose(candidates, policy)
    record = dict(policy=policy, policy_sha256=sha256(PROTOCOL.read_bytes()).hexdigest(), source_sha256=fingerprints(),
                  candidates=sorted(candidates, key=lambda c: (-c['score'], c['symbol'])),
                  selected={s: policy['timeframe'] for s in selected}, unavailable=unavailable, candidate_journal_sha256=journal_hashes)
    SELECTION.write_text(json.dumps(record, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(dict(stage='training_selection', selected=record['selected'], eligible=sum(c['eligible'] for c in candidates))), flush=True)
    return record
