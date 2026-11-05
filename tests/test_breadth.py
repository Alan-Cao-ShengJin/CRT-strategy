from dataclasses import asdict
from datetime import timedelta
import json

import pytest

from crt.calendar import load_sessions
from crt.models import Bar, INSTRUMENTS, MINUTE, timestamp
from crt.strategy import session_at
from research.breadth_study import config, stressed_pnl
from research.choch import Change, Pivot
from research.expanded_markets import EXTRA, ExpandedEngine, SpotStructure, StockAggregator, StockStructure, register
from research.breadth_report import pair_summary

AT = timestamp('2026-11-03T14:30:00Z')


@pytest.fixture
def markets(monkeypatch):
    from crt.models import Instrument
    for s, m in EXTRA.items():
        monkeypatch.setitem(INSTRUMENTS, s, Instrument(s, m.tick, m.point_value, m.exchange, m.group))
    return json.loads(open('research/choch_breadth_protocol.json').read())


def engine(symbol, rules, tmp_path):
    p = tmp_path/'sessions.json'
    rows = []
    for day in range(2):
        for at, event in ((AT+timedelta(days=day), 'open'), (AT+timedelta(days=day, hours=6, minutes=30), 'close')):
            rows.append(dict(product_code=symbol, timestamp=at.isoformat(), event=event))
    p.write_text(json.dumps(rows))
    e = ExpandedEngine(config(symbol, 'high_first', rules, str(p)), 5, 'next_open', 2., rules)
    e._contract(symbol, symbol)
    e._clock(AT)
    return e


def queue(e, price, stop, side=1):
    p = Pivot(1, price, AT-20*MINUTE, AT-5*MINUTE)
    c = Change(e.market.symbol, e.market.symbol, 5, side, price, stop, AT, p)
    e._queue(c, AT, session_at(AT, e.config))
    setup, change = e.orders.pop(e.market.symbol)
    assert e._enter_structural(setup, change, price, AT)
    return e.positions[e.market.symbol]


def test_optional_calendar_symbols_do_not_change_default_three(tmp_path):
    p = tmp_path/'calendar.json'
    p.write_text(json.dumps([dict(product_code='MYM', event=k, timestamp=t) for k,t in
                            [('open','2026-11-03T14:30:00Z'),('close','2026-11-03T21:00:00Z')]]))
    assert set(load_sessions(str(p))) == {'MNQ', 'MES', 'MGC'}
    assert len(load_sessions(str(p), ['MYM'])['MYM']) == 1


@pytest.mark.parametrize('symbol,tick_value', [('MYM', .5), ('M2K', .5), ('MCL', 1.), ('M6E', 1.25), ('SIL', 5.)])
def test_verified_extra_futures_multipliers(markets, symbol, tick_value):
    assert INSTRUMENTS[symbol].tick_value == pytest.approx(tick_value)


def test_stock_hourly_buckets_anchor_to_0930_and_drop_partial_close(markets):
    agg = StockAggregator(60, [(AT, AT+390*MINUTE)])
    completed = []
    for i in range(390):
        b = agg.push(Bar('AAPL', AT+i*MINUTE, 200, 201, 199, 200, 1, 'AAPL'))
        if b:
            completed.append(b)
    assert len(completed) == 6
    assert completed[0].start == AT and completed[-1].end == AT+360*MINUTE


def test_stock_structure_bridges_only_consecutive_scheduled_buckets(markets):
    intervals = [(AT, AT+390*MINUTE), (AT+timedelta(days=1), AT+timedelta(days=1)+390*MINUTE)]
    s = StockStructure(60, intervals)
    for i in range(6):
        s.push(Bar('AAPL', AT+60*i*MINUTE, 200, 201, 199, 200, 1, 'AAPL', 60))
    s.push(Bar('AAPL', AT+timedelta(days=1), 200, 201, 199, 200, 1, 'AAPL', 60))
    assert len(s.bars) == 7
    s.push(Bar('AAPL', AT+timedelta(days=1)+120*MINUTE, 200, 201, 199, 200, 1, 'AAPL', 60))
    assert len(s.bars) == 1  # missing the next full 60-minute bucket


@pytest.mark.parametrize('side,stop', [(1, 199), (-1, 201)])
def test_stock_whole_share_notional_cap_and_cost_risk(markets, tmp_path, side, stop):
    e = engine('AAPL', markets, tmp_path)
    p = queue(e, 200, stop, side)
    assert p.qty == int(p.qty) and p.entry*p.qty <= 50000
    assert p.risk <= 500+1e-8
    e._exit('AAPL', p.stop, AT+MINUTE, 'stop', market=True)
    t = e.trades[-1]
    assert t.fees == pytest.approx(t.qty*.01)
    assert 50000-e.cash == pytest.approx(t.risk)


def test_btc_fractional_size_percentage_fees_and_stop_budget(markets, tmp_path):
    side, stop = 1, 99000
    e = engine('BTCUSD', markets, tmp_path)
    p = queue(e, 100000, stop, side)
    assert p.entry*p.qty*.001 <= 50000
    assert p.qty*.001 < 1
    e._exit('BTCUSD', p.stop, AT+MINUTE, 'stop', market=True)
    t = e.trades[-1]
    assert t.fees == pytest.approx((t.entry+t.exit)*t.qty*.001*.001)
    assert t.net == pytest.approx((t.exit-t.entry)*side*t.qty*.001-t.fees)
    assert 50000-e.cash == pytest.approx(t.risk)
    assert t.risk <= 500+1e-8
    assert e.events[-1]['net'] == pytest.approx(t.net)


def test_btc_spot_rejects_short_orders(markets, tmp_path):
    e = engine('BTCUSD', markets, tmp_path)
    p = Pivot(1, 100000, AT-20*MINUTE, AT-5*MINUTE)
    c = Change('BTCUSD', 'BTCUSD', 5, -1, 100000, 101000, AT, p)
    e._queue(c, AT, session_at(AT, e.config))
    setup, change = e.orders.pop('BTCUSD')
    assert not e._enter_structural(setup, change, 100000, AT)
    assert not e.positions


def test_spot_structure_filters_bearish_signals_before_session_reservation(markets):
    prices = [100, 103, 110, 104, 98, 90, 99, 108, 120, 112, 106, 100, 107, 109, 98]
    s = SpotStructure(5)
    for i, price in enumerate(prices):
        assert s.push(Bar('BTCUSD', AT+i*5*MINUTE, price, price+1, price-1, price, 1, 'BTCUSD', 5)) is None


def screen_case(path, trades=100, profit=1000, factor=1.3, blocks=3, stress=500):
    return dict(path=path, stats=dict(trades=trades, net_pnl=profit, profit_factor=factor,
                positive_blocks=blocks, stress_net_pnl=stress, return_pct=profit/500,
                win_rate_pct=45., max_drawdown_pct=5., mean_planned_risk_pct=.8))


def test_consistency_screen_requires_every_condition_in_both_paths(markets):
    a, b = screen_case('high_first'), screen_case('low_first')
    assert pair_summary([a, b], markets)['status'] == 'passes'
    for field, value in [('net_pnl', -1), ('profit_factor', 1.1), ('positive_blocks', 2), ('stress_net_pnl', -1)]:
        c = screen_case('low_first');c['stats'][field] = value
        assert pair_summary([a, c], markets)['status'] == 'fails'


def test_small_winning_sample_and_missing_path_are_not_consistent_evidence(markets):
    a = screen_case('high_first')
    b = screen_case('low_first', trades=10, factor=3.)
    assert pair_summary([a, b], markets)['status'] == 'insufficient'
    assert pair_summary([a], markets)['status'] == 'unavailable'


def test_btc_target_exit_fee_uses_exit_notional_not_entry_fee_twice(markets, tmp_path):
    e = engine('BTCUSD', markets, tmp_path)
    p = queue(e, 100000, 99000)
    e._exit('BTCUSD', p.setup.target, AT+MINUTE, 'target')
    t = e.trades[-1]
    assert t.fees == pytest.approx((t.entry+t.exit)*t.qty*.001*.001)
    assert e.cash-50000 == pytest.approx(t.net)
    assert stressed_pnl(e.trades, e.market, markets) < t.net


def test_stock_exit_is_five_minutes_before_early_close(markets, tmp_path):
    e = engine('AAPL', markets, tmp_path)
    e.exchange_sessions['AAPL'] = [(AT, AT+210*MINUTE)]
    p = queue(e, 200, 199)
    assert not e._must_flatten(p, AT+204*MINUTE)
    assert e._must_flatten(p, AT+205*MINUTE)
