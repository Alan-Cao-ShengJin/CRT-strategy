"""Boundaries and frozen study design for the additional-history check."""
import json
from pathlib import Path

import pytest

from crt.models import timestamp
from research.expanded_markets import register
from research.five_minute_download import windows
from research.five_minute_validation import cases


def test_case_grid_has_no_unannounced_rule_search():
    policy = json.loads(Path('research/five_minute_validation_protocol.json').read_text())
    cs = cases(policy)
    assert len(cs) == len({c['tag'] for c in cs}) == 34
    assert sum(c['stress'] for c in cs) == 6
    assert sum(c['period'] == 'control' for c in cs) == 4
    assert {c['capital'] for c in cs} == {1000, 10000, 50000}
    assert all(set(c['basket']) <= {'MCL', 'MNQ'} for c in cs)
    assert all(c['end'] == policy['previous_start'] for c in cs if c['period'] == 'earlier')


def definition(ticker, last, first='2024-01-01', tick=.01):
    return dict(ticker=ticker, first_trade_date=first, last_trade_date=last, trade_tick_size=tick)


def test_monthly_roll_uses_reference_expiry_and_exact_boundary():
    register()
    refs = [definition('MCLJ5', '2025-03-19'), definition('MCLK5', '2025-04-21'),
            definition('MCLM5', '2025-05-19')]
    a, b = timestamp('2025-03-16T22:00:00Z'), timestamp('2025-05-01T00:00:00Z')
    ws = list(windows('MCL', a, b, refs))
    assert [w[0] for w in ws] == ['MCLK5', 'MCLM5']
    assert ws[0][1] == a and ws[-1][2] == b
    assert ws[0][2] == ws[1][1] == timestamp('2025-04-14T22:00:00Z')


def test_quarterly_roll_matches_existing_friday_convention():
    refs = [definition('MNQH5', '2025-03-21', tick=.25),
            definition('MNQM5', '2025-06-20', tick=.25),
            definition('MNQU5', '2025-09-19', tick=.25)]
    ws = list(windows('MNQ', timestamp('2025-03-16T22:00:00Z'), timestamp('2025-08-01T00:00:00Z'), refs))
    assert [w[0] for w in ws] == ['MNQM5', 'MNQU5']
    assert ws[0][2] == ws[1][1] == timestamp('2025-06-13T22:00:00Z')


def test_missing_or_off_tick_contract_reference_fails_closed():
    register()
    start, end = timestamp('2025-03-16T22:00:00Z'), timestamp('2025-05-01T00:00:00Z')
    with pytest.raises(RuntimeError, match='Incomplete'):
        list(windows('MCL', start, end, []))
    with pytest.raises(RuntimeError, match='Tick mismatch'):
        list(windows('MCL', start, end, [definition('MCLK5', '2025-04-21', tick=.1)]))


def test_monthly_report_compounds_daily_returns():
    from research.five_minute_validation_report import monthly
    actual = monthly([dict(day='2025-04-07', return_pct=10), dict(day='2025-04-08', return_pct=-10),
                      dict(day='2025-05-01', return_pct=5)])
    assert actual == pytest.approx({'2025-04': -1, '2025-05': 5})
