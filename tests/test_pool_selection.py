from copy import deepcopy
from datetime import timedelta
import json

import pytest

from crt.models import Bar, timestamp
from research.pool_select import choose, training_stats
from research.pool_portfolio import ClockPortfolio


@pytest.fixture
def policy():
    p = json.load(open('research/pool_protocol.json'))
    p['minimum_training_trades_each_path'] = 1
    return p


@pytest.fixture
def costs():
    return json.load(open('research/choch_breadth_protocol.json'))


def trade(day, net=20., exited=None, reason='target'):
    return dict(entry_time=day+'T15:00:00+00:00', exit_time=(exited or day)+'T16:00:00+00:00',
                entry=70., exit=70.2, net=net, qty=1, risk=10., reason=reason)


def test_later_profits_cannot_change_training_score_or_eligibility(policy, costs):
    early = [trade('2025-11-12'), trade('2026-03-12')]
    a = training_stats('MCL', early+[trade('2026-08-12', -1000000)], policy, costs)
    b = training_stats('MCL', early+[trade('2026-08-12', 1000000)], policy, costs)
    assert a == b and a['eligible']


def test_trade_outcome_must_be_completed_before_selection_cutoff(policy, costs):
    r = training_stats('MCL', [trade('2025-11-12'), trade('2026-04-30', 10000, exited='2026-05-01')], policy, costs)
    assert r['trades'] == 1 and r['net_pnl'] == 20.


@pytest.mark.parametrize('reason', ['end_of_data', 'roll_data_end_mark'])
def test_research_liquidations_cannot_inflate_training_selection(policy, costs, reason):
    r = training_stats('MCL', [trade('2025-11-12', reason=reason)], policy, costs)
    assert r['trades'] == 0 and not r['eligible']


def test_positive_base_pnl_with_negative_stress_is_rejected(policy, costs):
    p = deepcopy(policy);p['minimum_positive_training_blocks_each_path'] = 1
    r = training_stats('MCL', [trade('2025-11-12', 1.)], p, costs)
    assert r['net_pnl'] > 0 and r['stressed_pnl'] < 0 and 'training_cost_stress' in r['failed_conditions']


def test_one_profitable_time_block_is_insufficient(policy, costs):
    r = training_stats('MCL', [trade('2025-11-12', 1000.)], policy, costs)
    assert not r['eligible'] and 'training_time_blocks' in r['failed_conditions']


def test_selection_keeps_one_per_bucket_and_uses_deterministic_ties(policy):
    rows = [dict(symbol=s, eligible=True, score=score) for s,score in [('MNQ',5),('MES',6),('MCL',4),('USO',4),('SIL',3)]]
    assert choose(rows, policy) == ['MES','MCL','SIL']
    policy['maximum_selected_markets'] = 2
    assert choose(rows, policy) == ['MES','MCL']


def test_empty_pool_retains_zero_trade_reference_days():
    cap = json.load(open('research/portfolio_protocol.json'))
    rules = json.load(open('research/choch_breadth_protocol.json'))
    p = ClockPortfolio({}, 10000, 'high_first', cap, rules, {})
    at = timestamp('2026-11-03T15:00:00Z')
    for d in range(2):
        p.batch([Bar('MNQ', at+timedelta(days=d), 20000,20001,19999,20000,1,'MNQZ6')], '2026-11-03','2026-11-05')
    p.finish();r=p.result()
    assert r['stats']['market_days']==2 and r['stats']['zero_entry_days']==2
    assert r['stats']['return_pct']==0
