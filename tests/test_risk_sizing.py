from datetime import datetime

from crt.models import UTC
from research.risk_sizing_study import EquityRiskEngine, fractional_config
from test_execution import setup
from crt.config import Config
from crt.models import Bar, timestamp
from research.risk_sizing_study import run_case
from test_integration import fixture_bars, configuration


def test_percentage_budget_is_not_blocked_by_old_dollar_caps():
    c = fractional_config(Config().to_dict(), .01)
    e = EquityRiskEngine(c)
    qty, unit = e._size(setup(), 100, 90)
    assert qty*unit <= 500
    assert (qty+1)*unit > 500
    e.cash = 60000
    qty_after_gain, _ = e._size(setup(), 100, 90)
    assert qty_after_gain > qty


def test_two_percent_changes_whole_contract_sizing():
    first = EquityRiskEngine(fractional_config(Config().to_dict(), .01))
    second = EquityRiskEngine(fractional_config(Config().to_dict(), .02))
    one, unit = first._size(setup(), 100, 90)
    two, _ = second._size(setup(), 100, 90)
    assert two > one and two*unit <= 1000 < (two+1)*unit


def test_percentage_drawdown_uses_running_peak_not_initial_capital():
    e = EquityRiskEngine(fractional_config(Config().to_dict(), .01))
    at = datetime(2026, 11, 3, tzinfo=UTC)
    e.cash = 60000
    e._risk_check(at)
    e.cash = 48000
    e._risk_check(at)
    assert e.max_fractional_drawdown == .2


def test_contract_end_bookkeeping_is_explicit_not_a_cross_contract_fill():
    bars = fixture_bars()[:31] + [Bar("MNQ", timestamp("2026-11-04T14:00:00Z"), 150, 151, 149, 150, 10, "MNQH7")]
    cfg = fractional_config(configuration().to_dict(), .01)
    engine = run_case(cfg, bars, "2026-11-03", "2026-11-05")
    assert len(engine.roll_data_liquidations) == 1
    trade = engine.trades[0]
    assert trade.reason == "roll_data_end_mark"
    assert trade.exit == 108.75
    assert trade.contract == "MNQZ6"
