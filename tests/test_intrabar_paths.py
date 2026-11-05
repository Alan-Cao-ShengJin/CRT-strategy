from dataclasses import replace

import pytest

from crt.engine import Engine
from crt.models import MINUTE
from test_execution import cfg, setup, armed, bar, AT


def engine(path):
    return Engine(replace(cfg(), bar_model=path))


@pytest.mark.parametrize("path", ["high_first", "low_first"])
def test_entry_minute_target_is_credited_when_stop_is_not_reached(path):
    e = engine(path)
    armed(e)
    e.on_bar_batch([bar(o=99, h=116, l=98, c=114)])
    assert len(e.trades) == 1 and e.trades[0].reason == "target"


def test_pre_entry_low_cannot_stop_a_later_long():
    e = engine("low_first")
    armed(e)
    e.on_bar_batch([bar(o=99, h=116, l=80, c=110)])
    assert e.trades[0].reason == "target"
    e = engine("high_first")
    armed(e)
    e.on_bar_batch([bar(o=99, h=116, l=80, c=110)])
    assert e.trades[0].reason == "target"


def test_existing_position_ambiguous_exit_depends_on_path():
    for path, expected in (("high_first", "target"), ("low_first", "stop")):
        e = engine(path)
        s = armed(e)
        e._clock(AT)
        e.marks["MNQ"] = 100
        assert e._enter(s, 100, AT)
        e.on_bar_batch([bar(AT+MINUTE, 100, 120, 80, 100)])
        assert e.trades[0].reason == expected


def test_rebreak_entry_then_target_is_not_cancelled_using_future_high():
    e = engine("low_first")
    s = armed(e, setup(mode="rebreak"))
    s.phase = "armed"
    e.on_bar_batch([bar(o=99, h=116, l=98, c=114)])
    assert len(e.trades) == 1 and e.trades[0].reason == "target"


def test_rebreak_may_happen_in_one_minute_on_explicit_assumed_path():
    e = engine("high_first")
    armed(e, setup(mode="rebreak"))
    e.on_bar_batch([bar(o=99, h=102, l=98, c=101)])
    assert e.positions["MNQ"].entry == 100


def test_no_rebreak_if_initial_break_delivers_target_before_pullback():
    e = engine("high_first")
    armed(e, setup(mode="rebreak"))
    e.on_bar_batch([bar(o=99, h=116, l=98, c=101)])
    assert not e.positions and not e.trades and e.states["MNQ"].pending is None


def test_gap_over_entry_cap_is_not_filled_at_unavailable_trigger():
    e = engine("high_first")
    armed(e)
    e.on_bar_batch([bar(o=105, h=108, l=104, c=107)])
    assert not e.positions
    e.on_bar_batch([bar(AT+MINUTE, 104, 106, 100.5, 104)])
    assert e.positions["MNQ"].entry == 100.5


@pytest.mark.parametrize("path", ["high_first", "low_first"])
def test_short_entry_minute_target(path):
    e = engine(path)
    armed(e, setup(side=-1))
    e.on_bar_batch([bar(o=121, h=122, l=104, c=106)])
    assert e.trades[0].reason == "target"
