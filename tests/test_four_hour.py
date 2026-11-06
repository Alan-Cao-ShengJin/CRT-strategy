"""Economic and timing invariants for the independent video reconstruction."""
import json
from hashlib import sha256

import numpy as np
import pytest

from research.four_hour import (Candle, Series, assemble, bracket, buckets,
                                candles, epoch, execute, signals, sessions)
from research.four_hour_download import windows
from crt.models import timestamp


def fixture(prices, rolls=None, times=None):
    t = np.array(times if times is not None else np.arange(len(prices))*60+1000000200, dtype=np.int64)
    return Series(t, np.array(prices, dtype=float), np.zeros(len(prices)), rolls or {}, {})


def order(t, side=1, stop=90):
    return dict(at=t, side=side, stop=stop, pivot_time=t-1000, pivot_known=t-500,
                stop_pivot_known=t-500)


def test_confirmed_swings_and_prefix_causality():
    prices = [100, 103, 110, 104, 98, 90, 99, 108, 120, 112, 106, 100, 107, 109, 98]
    bars = [Candle(i*14400, (i+1)*14400, i, x, x+1, x-1, x) for i, x in enumerate(prices)]
    assert signals(bars[:14]) == []
    sig = signals(bars)[0]
    assert sig['side'] == -1 and sig['stop'] == 121.25
    assert sig['pivot_time'] == 11*14400
    assert sig['pivot_known'] == 14*14400 < sig['at']
    future = bars+[Candle(15*14400, 16*14400, 15, 200, 201, 199, 200)]
    assert signals(future)[0] == sig
    # An unexpected incomplete bucket clears stale trend information.
    assert not signals(bars[:14]+[None]+bars[14:])


@pytest.mark.parametrize('day,open_,close_', [
    ('2026-03-09', '2026-03-08T22:00:00Z', '2026-03-09T21:00:00Z'),
    ('2026-11-02', '2026-11-01T23:00:00Z', '2026-11-02T22:00:00Z')])
def test_session_anchor_daylight_saving_and_final_partial(day, open_, close_):
    a, b = epoch(open_), epoch(close_)
    parts = list(buckets([(day, [(a, b)])]))
    assert len(parts) == 6
    assert [len(x[2]) for x in parts] == [240]*5+[180]
    assert parts[0][0] == a and parts[-1][1] == b


def test_scheduled_halt_and_missing_minutes():
    a = epoch('2026-11-02T23:00:00Z'); end = a+23*3600
    spans = [(a, a+22*3600+15*60), (a+22*3600+30*60, end)]
    calendar = [('2026-11-03', spans)]
    expected = np.concatenate([x[2] for x in buckets(calendar)])
    s = fixture([[100, 101, 99, 100]]*len(expected), times=expected)
    cs, stats, _ = candles(s, calendar, strict=True)
    assert stats['rejected'] == 0 and stats['missing_minutes'] == 0
    assert len(cs) == 6
    # Six consecutive absent scheduled minutes reject the entire affected bucket.
    s.t = np.delete(s.t, np.arange(30, 36)); s.p = np.delete(s.p, np.arange(30, 36), axis=0)
    cs, stats, _ = candles(s, calendar)
    assert cs[0] is None and stats['rejected'] == 1


def test_stop_first_and_gap_fill():
    assert bracket(1, 90, 110, (100, 112, 88, 101), .25) == (89.75, 'stop', True)
    assert bracket(1, 90, 110, (80, 88, 78, 85), .25) == (79.75, 'gap_stop', False)
    assert bracket(-1, 110, 90, (120, 122, 118, 120), .25) == (120.25, 'gap_stop', False)
    assert bracket(1, 90, 110, (115, 116, 114, 115), .25) == (110., 'gap_target', False)


def test_no_signal_close_fill_and_fee_reconciliation():
    s = fixture([[100, 101, 99, 100], [101, 102, 100, 101], [101, 112, 100, 110]])
    at = int(s.t[1])
    r = execute(s, [order(at)], s.t, 'NQ', int(s.t[0]), int(s.t[-1])+60)
    tr = r['journal'][0]
    assert tr['entry_index'] == 1 and tr['entry'] == 101.25
    assert tr['target'] == 112.5 and tr['reason'] == 'data_end'
    assert r['net_pnl'] == pytest.approx(r['gross_pnl']-r['costs'])
    assert r['costs'] == 5 and r['net_pnl'] == 165


def test_roll_continuity_charges_both_legs_and_preserves_signal_levels(tmp_path):
    a = epoch('2026-09-10T19:59:00Z')
    records = []
    for name, times, p, start, end in [
        ('NQU6', [a, a+60], [[100, 101, 99, 100]]*2, a, a+60),
        ('NQZ6', [a+60, a+120], [[120, 121, 119, 120]]*2, a+60, a+180)]:
        file = tmp_path/(name+'.npz'); np.savez(file, t=times, p=p)
        from research.four_hour import iso
        records.append(dict(contract=name, file=str(file), sha256=sha256(file.read_bytes()).hexdigest(),
                            start=iso(start), end=iso(end)))
    s = assemble(dict(records=records))
    assert np.all(s.p[:, 0] == 100) and s.offset.tolist() == [0, 20, 20]
    r = execute(s, [order(a)], s.t, 'NQ', a, a+180)
    tr = r['journal'][0]
    assert tr['rolls'] == 1 and tr['roll_cost'] == 15
    assert tr['gross'] == -10 and tr['net'] == -30
    assert tr['exit_raw'] == 119.75
    # If the stop gaps at the roll open, exit the old contract without roll fees.
    s.p[1:] -= 15
    r = execute(s, [order(a)], s.t, 'NQ', a, a+180)
    tr = r['journal'][0]
    assert tr['rolls'] == 0 and tr['reason'] == 'gap_stop'
    assert tr['exit_raw'] == 84.75


def test_entry_cancels_for_unexpected_gap_but_bridges_scheduled_closure():
    t = np.array([1000000200, 1000003800], dtype=np.int64)
    s = fixture([[100, 101, 99, 100]]*2, times=t)
    expected = np.arange(t[0], t[-1]+60, 60)
    bad = execute(s, [order(int(t[0]+60))], expected, 'NQ', int(t[0]), int(t[-1])+60)
    assert bad['trades'] == 0 and bad['rejected']['late'] == 1
    good = execute(s, [order(int(t[0]+60))], t, 'NQ', int(t[0]), int(t[-1])+60)
    assert good['trades'] == 1


def test_declared_roll_precedes_expiration_and_is_simultaneous():
    ws = list(windows('ES', timestamp('2025-10-27T00:00:00Z'), timestamp('2026-11-05T00:00:00Z')))
    assert [r[0] for r in ws] == ['ESZ5', 'ESH6', 'ESM6', 'ESU6', 'ESZ6']
    assert ws[0][2] == timestamp('2025-12-11T21:00:00Z')
    assert all(a[2] == b[1] for a, b in zip(ws, ws[1:]))


def test_holiday_settlement_labels_do_not_join_two_sessions(tmp_path):
    rows = [dict(product_code='NQ', session_end_date='2025-05-27', event=k, timestamp=t)
            for k, t in [('open', '2025-05-25T22:00:00Z'),
                         ('pre_open', '2025-05-26T17:00:00Z'),
                         ('open', '2025-05-26T22:00:00Z'),
                         ('close', '2025-05-27T21:00:00Z')]]
    f = tmp_path/'calendar.json'; f.write_text(json.dumps(rows*2))
    ss = sessions(f, 'NQ')
    assert [x[0] for x in ss] == ['2025-05-26', '2025-05-27']
    assert [len(list(buckets([s]))) for s in ss] == [5, 6]
