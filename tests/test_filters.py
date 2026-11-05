from copy import deepcopy

from crt.models import Bar, MINUTE
from research.filter_study import FilterEngine, passes, select
from test_integration import configuration, fixture_bars, START


def test_features_are_available_at_c2_close_and_do_not_change_with_c3():
    e = FilterEngine(configuration())
    for b in fixture_bars()[:30]:
        e.on_bar_batch([b])
    setup = e.states["MNQ"].pending
    features = deepcopy(e.features[setup.id])
    assert features["available_at"] == setup.created.isoformat()
    assert features["reward_points"] == 11.75
    assert features["sweep_fraction"] == 2/30
    assert features["close_strength"] == .85
    assert passes("strong_rejection", features)
    assert not passes("minimum_reward_12_points", features)
    for b in fixture_bars()[30:]:
        e.on_bar_batch([b])
    assert e.features[setup.id] == features


def test_rejecting_a_setup_does_not_consume_session_and_later_setup_can_qualify():
    e = FilterEngine(configuration(), "minimum_reward_12_points")
    for b in fixture_bars()[:30]:
        e.on_bar_batch([b])
    assert e.states["MNQ"].pending is None
    assert not e.states["MNQ"].selected
    for i in range(30, 45):
        e.on_bar_batch([Bar("MNQ", START+i*MINUTE, 90, 94, 86, 90, 10, "MNQZ6")])
    pending = e.states["MNQ"].pending
    assert pending is not None
    assert e.features[pending.id]["reward_points"] == 13.75


def test_selection_enforces_minimum_sample_on_both_paths():
    protocol = {"candidates": {"tiny": "", "large": ""}, "paths": ["high_first", "low_first"],
                "minimum_training_subset_trades_each_path": 40, "shortlist_per_entry_mode": 2}
    screening = [{"filter": name, "entry_mode": "direct", "bar_path": path,
                  "trades": n, "wilson_lower": score}
                 for name, n, score in (("tiny", 10, .8), ("large", 50, .4))
                 for path in protocol["paths"]]
    selected = select(screening, "direct", protocol)
    assert [x["filter"] for x in selected] == ["large"]


def test_selection_uses_worse_path_instead_of_cherry_picking_better_path():
    protocol = {"candidates": {"fragile": "", "stable": ""}, "paths": ["high_first", "low_first"],
                "minimum_training_subset_trades_each_path": 40, "shortlist_per_entry_mode": 1}
    screening = [{"filter": name, "entry_mode": "rebreak", "bar_path": path,
                  "trades": 50, "wilson_lower": score}
                 for name, path, score in (("fragile", "high_first", .7), ("fragile", "low_first", .2),
                                           ("stable", "high_first", .4), ("stable", "low_first", .4))]
    assert select(screening, "rebreak", protocol)[0]["filter"] == "stable"
