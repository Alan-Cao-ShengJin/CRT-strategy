from dataclasses import replace

import pytest

pytest.importorskip("numpy")

from crt.config import Config
from crt.strategy import Chop, detect
from research.annual_study import metrics, session_days
from crt.models import Trade
from test_strategy import b, BASE, MINUTE


def test_mechanical_profile_really_disables_bias_in_both_directions():
    cfg = Config(bias_enabled=False, neutral_policy="first", chop_enabled=False)
    c1, c2 = b(), b(BASE+15*MINUTE, 95, 108, 88, 105)
    assert detect(c1, c2, -1, Chop(0, 0, 1, False), cfg).side == 1


def test_no_trade_metrics_are_explicit():
    protocol = {"bootstrap_seed": 7, "daily_block_bootstrap_repetitions": 10,
                "cost_stress_commission_per_side_usd": 1.5, "commission_per_side_per_contract_usd": .85}
    value = metrics([], session_days("2026-11-02", "2026-11-07"), protocol)
    assert value["trades"] == 0 and value["win_rate"] is None
    assert value["daily_block_bootstrap_95_net_pnl"] == [0., 0.]


def test_research_paths_are_valid_configurations():
    for path in ("high_first", "low_first", "legacy"):
        Config(bar_model=path).validate()
    with pytest.raises(ValueError, match="bar_model"):
        Config(bar_model="favorable").validate()


def test_cost_stress_and_bootstrap_use_actual_contract_counts():
    protocol = {"bootstrap_seed": 7, "daily_block_bootstrap_repetitions": 100,
                "cost_stress_commission_per_side_usd": 1.5, "commission_per_side_per_contract_usd": .85}
    trade = Trade("one", "MNQ", "MNQZ6", 1, 15, "direct", "2026-11-03T14:30:00Z",
                  "2026-11-03T14:31:00Z", 100, 115, 90, 115, 2, 60, 3.4, 56.6, 43.4, 56.6/43.4, "target")
    result = metrics([trade], ["2026-11-03"], protocol)
    assert result["net_pnl"] == 56.6
    assert result["cost_stress_net_pnl"] == pytest.approx(54.)
    assert result["daily_block_bootstrap_95_net_pnl"] == [56.6, 56.6]
    assert result["commission_break_even_per_side"] == 15.
