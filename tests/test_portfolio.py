from copy import deepcopy
from datetime import timedelta
import json

import pytest

from crt.models import Bar, INSTRUMENTS, Instrument, MINUTE, timestamp
from research.choch import Change, Pivot
from research.expanded_markets import EXTRA
from research.portfolio_study import Portfolio

AT = timestamp('2026-11-03T14:30:00Z')


@pytest.fixture
def portfolio(tmp_path, monkeypatch):
    for s, m in EXTRA.items():
        monkeypatch.setitem(INSTRUMENTS, s, Instrument(s, m.tick, m.point_value, m.exchange, m.group))
    rules = json.loads(open('research/choch_breadth_protocol.json').read())
    policy = json.loads(open('research/portfolio_protocol.json').read())
    def create(symbols, equity=100000):
        ready = {}
        for s in symbols:
            path = tmp_path/f'{s}.json'
            path.write_text(json.dumps([dict(product_code=s, event=kind, timestamp=at.isoformat())
                for day in range(2) for kind, at in [('open', AT+timedelta(days=day)),
                    ('close', AT+timedelta(days=day, hours=6, minutes=30))]]))
            ready[s] = dict(schedule_file=str(path))
        p = Portfolio({s: 5 for s in symbols}, equity, 'high_first', deepcopy(policy), rules, ready)
        p.day, p.day_start = '2026-11-03', equity
        for s, e in p.engines.items():
            e._contract(s, s)
            e._clock(AT)
        return p
    return create


def queue(p, symbol, price, stop):
    e = p.engines[symbol]
    pivot = Pivot(1, price, AT-20*MINUTE, AT-5*MINUTE)
    change = Change(symbol, symbol, 5, 1, price, stop, AT, pivot)
    e._queue(change, AT, '2026-11-03:new_york')
    return e.orders[symbol]


def enter(p, symbol, price, stop):
    setup, change = queue(p, symbol, price, stop)
    p.engines[symbol].orders.pop(symbol)
    return p.engines[symbol]._enter_structural(setup, change, price, AT)


def test_cash_and_open_marks_are_shared_across_asset_engines(portfolio):
    p = portfolio(['AAPL', 'MCL'])
    assert enter(p, 'AAPL', 200, 190)
    stock = p.positions['AAPL']
    assert p.engines['MCL'].cash == p.cash < p.initial
    p.engines['AAPL'].marks['AAPL'] = 205
    expected = p.cash+(205-stock.entry)*stock.qty
    assert p.engines['MCL'].equity == pytest.approx(expected)
    p.engines['AAPL']._exit('AAPL', 205, AT+MINUTE, 'test', market=True)
    assert p.engines['MCL'].equity == pytest.approx(p.initial+p.engines['AAPL'].trades[-1].net)


def test_same_minute_future_stock_high_cannot_finance_futures_entry(portfolio):
    outcomes = []
    for stock_high in (201, 230):
        p = portfolio(['AAPL', 'MCL'])
        queue(p, 'AAPL', 200, 190);queue(p, 'MCL', 70, 69)
        p.batch([Bar('AAPL', AT, 200, stock_high, 199, 200, 1, 'AAPL'),
                 Bar('MCL', AT, 70, 70.1, 69.9, 70, 1, 'MCL')], '2026-11-03', '2026-11-05')
        a = next(a for a in p.allocations if a['symbol'] == 'MCL')
        outcomes.append((a['equity_before'], a['qty'], p.equity))
    assert outcomes[0][:2] == outcomes[1][:2]
    assert outcomes[1][2] > outcomes[0][2]


def test_index_concentration_and_total_position_cap_are_enforced(portfolio):
    p = portfolio(['MNQ', 'MES', 'MCL', 'AAPL'])
    assert enter(p, 'MNQ', 20000, 19900)
    assert not enter(p, 'MES', 6000, 5990)
    assert p.rejections['equity_index_concentration'] == 1
    assert enter(p, 'MCL', 70, 69)
    assert not enter(p, 'AAPL', 200, 190)
    assert p.rejections['max_positions'] == 1
    assert sum(x.risk for x in p.positions.values()) <= .02*p.initial


def test_full_initial_margin_and_cash_notional_share_one_reserve(portfolio):
    p = portfolio(['SIL', 'AAPL'], equity=10000)
    assert enter(p, 'SIL', 30, 29.99)
    assert p.positions['SIL'].qty == 1  # margin, not risk budget, binds
    assert p.reserved() == 7370
    assert enter(p, 'AAPL', 200, 199)
    assert p.positions['AAPL'].qty <= 3
    assert p.reserved() <= .8*p.initial


def test_btc_spot_cash_reserve_fees_and_shared_risk(portfolio):
    p = portfolio(['BTCUSD', 'MCL'])
    assert enter(p, 'BTCUSD', 100000, 99000)
    b = p.positions['BTCUSD']
    assert b.qty*.001 < 1 and b.risk <= .01*p.initial
    assert p.reserved() < .8*p.initial
    p.engines['BTCUSD']._exit('BTCUSD', b.setup.target, AT+MINUTE, 'target')
    trade = p.engines['BTCUSD'].trades[-1]
    assert trade.fees == pytest.approx((trade.entry+trade.exit)*trade.qty*.001*.001)
    assert p.cash == pytest.approx(p.initial+trade.net)


def test_daily_halt_liquidates_at_next_observed_open_not_prior_mark(portfolio):
    p = portfolio(['MCL'])
    assert enter(p, 'MCL', 70, 69)
    p.cash -= 4000  # another loss/gap pushes the shared account through its limit
    p.observe(AT)
    assert p.halt == 'portfolio_daily_loss'
    p.batch([Bar('MCL', AT+MINUTE, 68, 68.1, 67.9, 68, 1, 'MCL')], '2026-11-03', '2026-11-05')
    t = p.engines['MCL'].trades[-1]
    assert t.reason == 'portfolio_daily_loss' and t.exit == pytest.approx(67.99)
    assert t.exit_time == (AT+MINUTE).isoformat()


def test_daily_return_includes_zero_trade_market_days(portfolio):
    p = portfolio(['MCL'])
    for d in range(2):
        p.batch([Bar('MCL', AT+timedelta(days=d), 70, 70.1, 69.9, 70, 1, 'MCL')], '2026-11-03', '2026-11-05')
    p.finish()
    r = p.result()
    assert r['stats']['market_days'] == 2 and r['stats']['zero_entry_days'] == 2
    assert r['stats']['mean_daily_return_pct'] == 0
