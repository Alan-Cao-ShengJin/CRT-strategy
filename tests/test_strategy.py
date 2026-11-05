from datetime import timedelta
from dataclasses import replace

import pytest

from crt.config import Config
from crt.models import Bar, MINUTE, timestamp
from crt.strategy import Aggregator, Bias, Chop, detect, session_at, trading_day, chop_metrics

BASE = timestamp("2026-11-03T14:00:00Z")


def b(start=BASE, o=100, h=120, l=90, c=110, minutes=15, symbol="MNQ"):
    return Bar(symbol, start, o, h, l, c, 10, symbol + "Z6", minutes)


def test_bull_and_bear_are_mirrored():
    cfg = Config(chop_enabled=False)
    clean = Chop(.2, .1, .8, False)
    c1 = b()
    c2 = b(BASE + 15 * MINUTE, 95, 108, 88, 105)
    s = detect(c1, c2, 1, clean, cfg)
    assert s.side == 1 and s.trigger == 108.25 and s.target == 120
    assert s.created == BASE + 30 * MINUTE
    assert s.expires == BASE + 45 * MINUTE
    c2 = b(BASE + 15 * MINUTE, 115, 122, 102, 110)
    s = detect(c1, c2, -1, clean, cfg)
    assert s.side == -1 and s.trigger == 101.75 and s.target == 90


@pytest.mark.parametrize("o,h,l,c", [(95, 121, 88, 105), (95, 108, 90, 105), (95, 108, 88, 90), (95, 121, 88, 120)])
def test_invalid_double_sweep_touch_or_boundary_close(o, h, l, c):
    assert detect(b(), b(BASE + 15 * MINUTE, o, h, l, c), 1, Chop(0, 0, 1, False), Config()) is None


def test_bias_filter_neutral_policy_and_chop():
    c1, c2 = b(), b(BASE + 15 * MINUTE, 95, 108, 88, 105)
    clean = Chop(0, 0, 1, False)
    assert detect(c1, c2, -1, clean, Config()) is None
    assert detect(c1, c2, 0, clean, Config()) is None
    assert detect(c1, c2, 0, clean, Config(neutral_policy="first"))
    assert detect(c1, c2, 1, Chop(1, 1, 0, True), Config()) is None


def test_candles_do_not_aggregate_across_gaps_or_contracts():
    agg = Aggregator(15)
    outputs = []
    for i in range(30):
        if i == 5:
            continue
        r = agg.push(b(BASE + i * MINUTE, minutes=1))
        if r:
            outputs.append(r)
    assert len(outputs) == 1 and outputs[0].start == BASE + 15 * MINUTE


def test_sessions_follow_dst_and_cme_trade_date():
    cfg = Config()
    assert session_at(timestamp("2026-10-30T13:30:00Z"), cfg).endswith("new_york")
    assert session_at(timestamp("2026-11-03T14:30:00Z"), cfg).endswith("new_york")
    assert session_at(timestamp("2026-11-03T17:00:00Z"), cfg) is None
    assert session_at(timestamp("2026-11-07T14:30:00Z"), cfg) is None
    assert trading_day(timestamp("2026-11-02T01:00:00Z")) == "2026-11-02"


def test_bias_never_sees_future_bars_and_expires_reversal():
    cfg = Config(bias_lookback=3, bias_ttl_hours=1)
    bias = Bias(cfg)
    for i in range(3):
        bias.push(b(BASE + i * 60 * MINUTE, 100, 110, 90, 100, 60))
    assert bias.get(BASE + 180 * MINUTE) == 0
    sweep = b(BASE + 180 * MINUTE, 95, 108, 85, 105, 60)
    bias.push(sweep)
    assert bias.get(sweep.end) == 1
    assert bias.get(sweep.end + timedelta(hours=2)) == 0


def test_chop_alternating_wicks_vs_clean_trend():
    cfg = Config(chop_lookback=8)
    messy = [b(BASE + i * MINUTE, 100, 105, 95, 100 + (.25 if i % 2 else -.25), 1) for i in range(8)]
    assert chop_metrics(messy, cfg).blocked
    clean = [b(BASE + i * MINUTE, 100+i, 102+i, 100+i, 102+i, 1) for i in range(8)]
    assert not chop_metrics(clean, cfg).blocked
