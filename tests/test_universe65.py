import json

from research import pool_markets
from research.pool_select import choose
from research.universe65 import policy,selection_policy,universe,ready_path


def test_expansion_is_65_unique_instruments_and_keeps_original29():
    symbols=universe()
    assert len(symbols)==len(set(symbols))==65
    assert symbols[:29]==pool_markets.universe()
    assert len(policy()['added_symbols'])==36


def test_old_datasets_are_not_redirected_and_new_ones_are_isolated():
    for symbol in pool_markets.universe():
        assert ready_path(symbol)==pool_markets.ready_path(symbol)
    for symbol in policy()['added_symbols']:
        assert 'choch_universe65' in str(ready_path(symbol))


def test_selection_thresholds_and_training_dates_stay_fixed():
    old=json.loads(pool_markets.PROTOCOL.read_text());new=selection_policy()
    assert all(new[k]==v for k,v in old.items() if k!='exposure_buckets')
    assert set(new['exposure_buckets'])==set(universe())
    assert all(new['exposure_buckets'][s]==b for s,b in old['exposure_buckets'].items())


def test_topfive_does_not_silently_enable_all_eligible_symbols():
    p=selection_policy()
    candidates=[dict(symbol=s,score=100-i,eligible=True) for i,s in enumerate(universe())]
    selected=choose(candidates,p)
    assert len(selected)==5
    assert len({p['exposure_buckets'][s] for s in selected})==5


def test_fast_position_index_preserves_parent_sum_order_and_removal(tmp_path):
    from types import SimpleNamespace
    from research.universe65_fast import FastClockPortfolio
    from research.portfolio_study import Portfolio
    costs=json.load(open('research/choch_breadth_protocol.json'));capital=json.load(open('research/portfolio_protocol.json'))
    path=tmp_path/'cal.json';path.write_text('[]')
    p=FastClockPortfolio({'MNQ':5,'MGC':5},50000,'high_first',capital,costs,{s:{'schedule_file':str(path)} for s in ('MNQ','MGC')})
    for s,price in [('MGC',2500),('MNQ',20000)]:
        p.engines[s].marks[s]=price+1
        p.engines[s].positions[s]=SimpleNamespace(entry=price,qty=2,risk=10.,setup=SimpleNamespace(side=1))
        assert p.equity==Portfolio.equity.fget(p)
        assert list(p.positions)==list(Portfolio.positions.fget(p))
    assert p.active==['MNQ','MGC']
    p.engines['MNQ'].positions.pop('MNQ')
    assert p.active==['MGC'] and p.equity==Portfolio.equity.fget(p)
    p.engines['MGC'].positions.clear()
    assert p.active==[] and p.equity==50000


def test_fast_and_original_accounts_match_entries_costs_and_exits(tmp_path):
    from dataclasses import asdict
    from crt.models import MINUTE,timestamp
    from crt.strategy import session_at
    from research.choch import Change,Pivot
    from research.pool_portfolio import ClockPortfolio
    from research.universe65_fast import FastClockPortfolio
    costs=json.load(open('research/choch_breadth_protocol.json'));capital=json.load(open('research/portfolio_protocol.json'))
    at=timestamp('2026-11-03T14:30:00Z');rows=[]
    for s in ('MNQ','MGC'):
        rows.extend([dict(product_code=s,event='open',timestamp=at.isoformat()),dict(product_code=s,event='close',timestamp=(at+390*MINUTE).isoformat())])
    file=tmp_path/'cal.json';file.write_text(json.dumps(rows));ready={s:{'schedule_file':str(file)} for s in ('MNQ','MGC')}
    results=[]
    for klass in (ClockPortfolio,FastClockPortfolio):
        p=klass({'MNQ':5,'MGC':5},50000,'high_first',capital,costs,ready)
        for s,price,stop in [('MNQ',20000,19950),('MGC',2500,2490)]:
            e=p.engines[s];e._contract(s,s);e._clock(at)
            pivot=Pivot(1,price,at-20*MINUTE,at-5*MINUTE)
            c=Change(s,s,5,1,price,stop,at,pivot)
            e._queue(c,at,session_at(at,e.config));setup,change=e.orders.pop(s)
            assert e._enter_structural(setup,change,price,at)
        p.engines['MNQ'].marks['MNQ']=20020;p.engines['MGC'].marks['MGC']=2499
        marked=p.equity
        p.engines['MNQ']._exit('MNQ',20010,at+MINUTE,'test',market=True)
        p.engines['MGC']._exit('MGC',2490,at+2*MINUTE,'stop',market=True)
        results.append((marked,p.cash,p.allocations,[asdict(t) for e in p.engines.values() for t in e.trades]))
    assert results[0]==results[1]
