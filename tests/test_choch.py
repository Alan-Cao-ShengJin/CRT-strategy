from datetime import timedelta

import pytest

from crt.config import Config
from crt.models import Bar, MINUTE, timestamp
from crt.strategy import session_at
from research.choch import Change, ChochEngine, Pivot, Structure

AT = timestamp('2026-11-03T14:30:00Z')


def candle(at, price, minutes=5):
    return Bar('MNQ', at, price, price+1, price-1, price, 100, 'MNQZ6', minutes)


def structure_bars(mirror=False):
    prices = [100, 103, 110, 104, 98, 90, 99, 108, 120, 112, 106, 100, 107, 109, 98]
    return [candle(AT+MINUTE*5*i, 220-price if mirror else price) for i, price in enumerate(prices)]


def engine(mode='next_open', path='high_first'):
    cfg = Config(symbols=['MNQ'], bias_enabled=False, chop_enabled=False, neutral_policy='first',
                 bar_model=path, initial_equity=50000, risk_fraction=.01,
                 risk_per_trade=1e12, max_open_risk=1e12, max_daily_loss=1e12,
                 max_drawdown=1e12, max_contracts=150, max_positions=1,
                 min_stop_ticks=1, min_target_ticks=1)
    e = ChochEngine(cfg, mode=mode)
    e._contract('MNQ', 'MNQZ6')
    return e


def change(side=1):
    pivot = Pivot(3, 100, AT-20*MINUTE, AT-5*MINUTE)
    return Change('MNQ', 'MNQZ6', 5, side, 100, 90 if side == 1 else 110, AT, pivot)


def queue(e, side=1):
    c = change(side)
    e._queue(c, AT, session_at(AT, e.config))


def test_pivot_is_unavailable_until_two_right_candles_close():
    s = Structure(5)
    bars = structure_bars()
    for b in bars[:13]:
        assert s.push(b) is None
    assert len(s.lows) == 1
    assert s.push(bars[13]) is None
    assert len(s.lows) == 2
    assert s.lows[-1].time == bars[11].start
    assert s.lows[-1].known_at == bars[13].end


@pytest.mark.parametrize('mirror,side', [(False, -1), (True, 1)])
def test_character_change_requires_established_trend_then_counter_close(mirror, side):
    s = Structure(5)
    signals = [x for b in structure_bars(mirror) if (x := s.push(b)) is not None]
    assert len(signals) == 1
    c = signals[0]
    assert c.side == side and c.pivot.known_at < c.confirmed
    assert c.stop == (121.25 if side == -1 else 98.75)


def test_wick_beyond_pivot_without_closing_break_is_not_choch():
    s = Structure(5)
    bars = structure_bars()
    for b in bars[:-1]:
        s.push(b)
    b = bars[-1]
    assert s.push(Bar('MNQ', b.start, 105, 110, 97, 105, 100, 'MNQZ6', 5)) is None


def test_broken_pivot_is_consumed_not_repeated_on_later_recross():
    s = Structure(5)
    bars = structure_bars()
    for b in bars:
        s.push(b)
    assert s.push(candle(bars[-1].end, 105)) is None
    assert s.push(candle(bars[-1].end+5*MINUTE, 98)) is None


def test_missing_structure_bucket_resets_trend_history():
    s = Structure(5)
    bars = structure_bars()
    for b in bars[:-1]:
        s.push(b)
    assert s.push(candle(bars[-1].end+5*MINUTE, 98)) is None
    assert len(s.bars) == 1 and not s.highs and not s.lows


@pytest.mark.parametrize('side,path,reason', [(1, 'high_first', 'target'), (1, 'low_first', 'stop'),
                                             (-1, 'high_first', 'stop'), (-1, 'low_first', 'target')])
def test_entry_bar_bracket_obeys_assumed_path(side, path, reason):
    e = engine(path=path)
    queue(e, side)
    e.on_bar_batch([Bar('MNQ', AT, 100, 122 if side == 1 else 111,
                        89 if side == 1 else 78, 101, 100, 'MNQZ6')])
    assert e.trades[0].reason == reason
    assert e.trades[0].stop == (90 if side == 1 else 110)
    assert e.trades[0].entry_time == AT.isoformat()
    assert e.entry_risk_fractions[0] <= .01+1e-8


def test_full_signal_pipeline_never_enters_on_unfinished_break_candle():
    e = engine()
    bars = structure_bars()
    for b in bars:
        for i in range(5):
            e.on_bar_batch([Bar('MNQ', b.start+i*MINUTE, b.open, b.high, b.low, b.close, 20, 'MNQZ6')])
            assert not e.positions and not e.trades
    assert e.orders['MNQ'][0].created == bars[-1].end
    e.on_bar_batch([candle(bars[-1].end, 98, 1)])
    assert e.positions['MNQ'].time == bars[-1].end
    assert e.positions['MNQ'].setup.side == -1


def test_missing_immediate_entry_minute_cancels_not_delays_fill():
    e = engine()
    queue(e)
    e.on_bar_batch([candle(AT+MINUTE, 100, 1)])
    assert not e.positions and not e.trades and not e.orders


def test_gap_through_frozen_stop_rejects_entry():
    e = engine()
    queue(e)
    e.on_bar_batch([Bar('MNQ', AT, 89, 101, 88, 100, 100, 'MNQZ6')])
    assert not e.positions and not e.trades


def test_retest_waits_for_close_then_enters_following_open():
    e = engine(mode='retest_close')
    e.waiting['MNQ'] = (change(), session_at(AT, e.config))
    for i in range(5):
        e.on_bar_batch([Bar('MNQ', AT+i*MINUTE, 101, 103, 99, 102, 100, 'MNQZ6')])
        assert not e.positions
    assert 'MNQ' in e.orders
    e.on_bar_batch([Bar('MNQ', AT+5*MINUTE, 104, 105, 103, 104, 100, 'MNQZ6')])
    assert e.positions['MNQ'].time == AT+5*MINUTE
    assert e.positions['MNQ'].entry == 104.25
    assert e.positions['MNQ'].stop == 90


def test_stop_before_retest_confirmation_cancels_setup():
    e = engine(mode='retest_close')
    e.waiting['MNQ'] = (change(), session_at(AT, e.config))
    for i in range(5):
        e.on_bar_batch([Bar('MNQ', AT+i*MINUTE, 101, 103, 89 if i == 1 else 99, 102, 100, 'MNQZ6')])
    assert not e.waiting and not e.orders and not e.positions


def test_retest_after_three_timeframe_candles_is_expired():
    e = engine(mode='retest_close')
    e.waiting['MNQ'] = (change(), session_at(AT, e.config))
    for i in range(20):
        low = 99 if i >= 15 else 101
        e.on_bar_batch([Bar('MNQ', AT+i*MINUTE, 102, 103, low, 102, 100, 'MNQZ6')])
    assert not e.waiting and not e.orders and not e.positions


def test_contract_change_clears_pending_choch_and_structure():
    e = engine()
    queue(e)
    e.structures['MNQ'].push(candle(AT, 100))
    e._contract('MNQ', 'MNQH7')
    assert not e.orders and not e.waiting and not e.structures['MNQ'].bars
