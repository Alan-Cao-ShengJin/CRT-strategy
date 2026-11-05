from datetime import timedelta

import pytest

from crt.config import Config
from crt.engine import Engine
from crt.models import Bar, Setup, Tick, MINUTE, timestamp, INSTRUMENTS

AT = timestamp("2026-11-03T14:30:00Z")


def cfg(**kwargs):
    return Config(chop_enabled=False, neutral_policy="first", slippage_ticks=0, bar_model="legacy", **kwargs)


def setup(symbol="MNQ", side=1, mode="direct", at=AT):
    return Setup("test:"+symbol, symbol, symbol+"Z6", side, 15, at, at + 15*MINUTE,
                 100 if side == 1 else 120, 115 if side == 1 else 105,
                 "2026-11-03:new_york", mode, side)


def armed(engine, s=None):
    s = s or setup()
    engine.states[s.symbol].pending = s
    return s


def bar(at=AT, o=99, h=101, l=98, c=100, symbol="MNQ"):
    return Bar(symbol, at, o, h, l, c, 10, symbol+"Z6")


def tick(price, seconds, symbol="MNQ"):
    return Tick(symbol, AT+timedelta(seconds=seconds), price, 1, symbol+"Z6", str(seconds))


def test_direct_bracket_and_accounting_with_costs():
    e = Engine(cfg())
    armed(e)
    e.on_bar_batch([bar()])
    p = e.positions["MNQ"]
    assert p.entry == 100 and p.stop == 90 and p.qty == 4
    assert p.risk <= e.config.risk_per_trade
    e.on_bar_batch([bar(AT+MINUTE, 110, 116, 109, 115)])
    t = e.trades[0]
    assert t.reason == "target" and t.fees == pytest.approx(6.8)
    assert e.cash == pytest.approx(e.config.initial_equity + t.net)


@pytest.mark.parametrize("side", [1, -1])
def test_stop_wins_ambiguous_bar(side):
    e = Engine(cfg())
    s = armed(e, setup(side=side))
    e._clock(AT)
    e.marks[s.symbol] = s.trigger
    assert e._enter(s, s.trigger, AT)
    e.on_bar_batch([bar(AT+MINUTE, 110, 135, 80, 110)])
    assert e.trades[0].reason == "stop"


def test_entry_bar_cannot_claim_favorable_target():
    e = Engine(cfg())
    armed(e)
    e.on_bar_batch([bar(o=99, h=116, l=95, c=110)])
    assert not e.trades and "MNQ" in e.positions


def test_c3_expiry_no_late_fill():
    e = Engine(cfg())
    armed(e)
    e.on_bar_batch([bar(AT+15*MINUTE, 99, 101, 98, 100)])
    assert not e.positions and e.states["MNQ"].pending is None


def test_gap_over_limit_waits_for_revisit():
    e = Engine(cfg())
    armed(e)
    e.on_bar_batch([bar(o=105, h=108, l=104, c=107)])
    assert not e.positions and e.states["MNQ"].pending.activated
    e.on_bar_batch([bar(AT+MINUTE, 104, 106, 100.5, 104)])
    assert e.positions["MNQ"].entry == 100.5


def test_tick_rebreak_requires_observed_break_pullback_rebreak():
    e = Engine(cfg())
    armed(e, setup(mode="rebreak"))
    e.on_tick(tick(100, 0))
    assert not e.positions
    e.on_tick(tick(99.75, 1))
    assert not e.positions
    e.on_tick(tick(100, 2))
    assert e.positions["MNQ"].entry == 100


def test_minute_rebreak_never_invents_intrabar_sequence():
    e = Engine(cfg())
    armed(e, setup(mode="rebreak"))
    e.on_bar_batch([bar(o=99, h=101, l=98, c=99)])
    assert not e.positions and e.states["MNQ"].pending.phase == "broken"
    e.on_bar_batch([bar(AT+MINUTE, 99, 101, 98, 99)])
    assert not e.positions and e.states["MNQ"].pending.phase == "armed"
    e.on_bar_batch([bar(AT+2*MINUTE)])
    assert "MNQ" in e.positions


def test_no_chase_after_target_delivered():
    e = Engine(cfg())
    armed(e, setup(mode="rebreak"))
    e.on_tick(tick(116, 1))
    assert not e.positions and e.states["MNQ"].pending is None


def test_correlated_equity_limit():
    e = Engine(cfg())
    armed(e)
    armed(e, setup("MES"))
    e.on_bar_batch([bar(), bar(symbol="MES")])
    assert len(e.positions) == 1
    assert any(x.get("reason") == "correlated_equity_limit" for x in e.events)


def test_fractional_contract_sizing_never_rounds_up():
    e = Engine(cfg(risk_per_trade=1))
    armed(e)
    e.on_bar_batch([bar()])
    assert not e.positions


def test_gapped_stop_executes_at_observed_price():
    e = Engine(cfg())
    armed(e)
    e.on_tick(tick(100, 0))
    e.on_tick(tick(85, 1))
    assert e.trades[0].exit == 85 and e.trades[0].reason == "stop"


def test_feed_halt_cancels_entries_and_exits_on_new_price():
    e = Engine(cfg())
    armed(e)
    e.on_tick(tick(100, 0))
    e.feed_halted = True
    e.on_tick(tick(99, 1))
    assert not e.positions and e.trades[0].reason == "feed_halt"


def test_daily_loss_and_drawdown_halt():
    e = Engine(cfg(max_daily_loss=50, max_drawdown=80))
    armed(e)
    e.on_tick(tick(100, 0))
    e.on_tick(tick(80, 1))
    assert e.halt_reason == "max_drawdown" and e.permanent_halt


def test_roll_with_open_position_rejected():
    e = Engine(cfg())
    armed(e)
    e.on_tick(tick(100, 0))
    with pytest.raises(ValueError, match="roll"):
        e.on_tick(Tick("MNQ", AT+timedelta(seconds=1), 105, 1, "MNQH7"))


def test_duplicate_bars_rejected():
    e = Engine(cfg())
    e.on_bar_batch([bar()])
    with pytest.raises(ValueError, match="Duplicate"):
        e.on_bar_batch([bar()])


def test_forced_flatten_uses_next_available_price():
    e = Engine(cfg())
    armed(e)
    e.on_tick(tick(100, 0))
    e.on_tick(Tick("MNQ", timestamp("2026-11-03T21:55:00Z"), 101, 1, "MNQZ6"))
    assert e.trades[0].reason == "daily_flatten"


def test_tick_rounding_meets_nominal_rr():
    for symbol in ("MNQ", "MES", "MGC"):
        e = Engine(cfg(risk_per_trade=200))
        s = setup(symbol)
        e._clock(AT)
        e.marks[symbol] = 100
        assert e._enter(s, 100, AT)
        p = e.positions[symbol]
        assert INSTRUMENTS[symbol].on_grid(p.stop)
        assert (s.target-p.entry)/(p.entry-p.stop) >= 1.5 - 1e-8
