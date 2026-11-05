import json
from pathlib import Path

import pytest

from crt.models import Bar, MINUTE, timestamp
from research.topstep_evaluation import TopstepEvaluation, evaluation_config
from test_execution import setup

AT = timestamp("2026-11-03T14:30:00Z")


def engine(path="high_first"):
    rules = json.loads(Path("research/topstep_eval_protocol.json").read_text())
    cfg = evaluation_config(path, rules)
    e = TopstepEvaluation(cfg, rules, "2026-11-03")
    e.activate(AT)
    e._clock(AT)
    return e


def test_mll_does_not_trail_intraday_profits_but_rises_at_day_end():
    e = engine()
    e.cash = 152000
    e._risk_check(AT)
    assert e.floor == 145500
    e.finalize_day()
    assert e.floor == 147500
    e.prop_day = "2026-11-04"
    e.prop_day_start_cash = e.cash
    e.cash = 151000
    e.finalize_day()
    assert e.floor == 147500


def test_floor_locks_at_starting_balance():
    e = engine()
    e.cash = 155000
    e.finalize_day()
    assert e.floor == 150000


def test_floor_touch_fails_even_if_balance_later_recovers():
    e = engine()
    e.cash = e.floor
    e._risk_check(AT)
    assert e.status == "failed_mll"
    e.cash = 150000
    e._risk_check(AT+MINUTE)
    assert e.status == "failed_mll"


def test_consistency_prevents_one_day_target_pass_and_requires_extra_profit():
    e = engine()
    e.cash = 159000
    e.day_entries = 2
    e.finalize_day()
    assert e.status == "active"
    assert e.day_results[-1]["required_total_profit"] == pytest.approx(9000/.55)
    e.prop_day = "2026-11-04"
    e.prop_day_start_cash = e.cash
    e.cash = 166500
    e.day_entries = 1
    e.finalize_day()
    assert e.status == "passed"


def test_two_balanced_profit_days_pass_at_target():
    e = engine()
    e.cash = 154500
    e.day_entries = 1
    e.finalize_day()
    e.prop_day = "2026-11-04"
    e.prop_day_start_cash = e.cash
    e.cash = 159000
    e.day_entries = 1
    e.finalize_day()
    assert e.status == "passed"
    assert e.terminal_time == timestamp("2026-11-04T21:10:00Z")


def test_150_contract_cap_can_reduce_risk_below_one_percent():
    e = engine()
    qty, unit = e._size(setup(), 100, 99.75)
    assert qty == 150 and qty*unit < 1500


def test_stop_new_entries_when_flat_and_eligible_pending_day_end():
    e = engine()
    e.traded_days = 1
    e.best_day = 4500
    e.prop_day_start_cash = 154500
    e.cash = 157500
    e.states["MNQ"].contract = "MNQZ6"
    e.marks["MNQ"] = 100
    s = setup()
    assert e._enter(s, 100, AT)
    e._exit("MNQ", 115, AT+MINUTE, "target")
    assert e.ready_for_eod and e.status == "active"
    assert not e._enter(setup(), 100, AT+2*MINUTE)
    assert not e._accept_setup(s, None, None, None)
    e.finalize_day()
    assert e.status == "passed"


def test_unrealized_floor_level_is_visited_before_stop_and_later_recovery():
    e = engine("low_first")
    s = setup()
    e.states["MNQ"].contract = "MNQZ6"
    e.marks["MNQ"] = 100
    assert e._enter(s, 100, AT)
    e.floor = e.cash-100
    e.on_bar_batch([Bar("MNQ", AT+MINUTE, 100, 120, 80, 115, 10, "MNQZ6")])
    assert e.status == "failed_mll"
    assert e.trades[0].reason == "prop_mll"
    assert e.trades[0].exit > e.trades[0].stop


def test_position_is_closed_at_operational_cutoff():
    e = engine()
    s = setup()
    e.states["MNQ"].contract = "MNQZ6"
    e.marks["MNQ"] = 100
    assert e._enter(s, 100, AT)
    e.consume(Bar("MNQ", timestamp("2026-11-03T21:08:00Z"), 101, 102, 100, 101, 10, "MNQZ6"))
    assert not e.positions and e.trades[0].reason == "daily_flatten"


def test_gap_past_exit_deadline_is_censored_without_inventing_an_exit():
    e = engine()
    s = setup()
    e.states["MNQ"].contract = "MNQZ6"
    e.marks["MNQ"] = 100
    assert e._enter(s, 100, AT)
    e.consume(Bar("MNQ", timestamp("2026-11-03T21:20:00Z"), 101, 102, 100, 101, 10, "MNQZ6"))
    assert e.status == "data_gap_censored"
    assert not e.trades and "MNQ" in e.positions
