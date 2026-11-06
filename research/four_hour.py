"""Causal four-hour CHoCH reconstruction, kept separate from intraday engines.

Run: python -m research.four_hour_download; python -m research.four_hour
Prices are dated outright futures. Only signal coordinates are roll-adjusted;
every open-position roll is charged as a close and reopen.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import gzip
import json
from pathlib import Path

import numpy as np

from crt.models import NY, UTC
from research.four_hour_download import DEST, PROTOCOL

OUT = Path('research/results/four_hour')
TICK = .25
SPECS = {'NQ': (20., 2.50), 'ES': (50., 2.50),
         'MNQ_proxy': (2., .85), 'MES_proxy': (5., .85)}


def iso(t):
    return datetime.fromtimestamp(int(t), UTC).isoformat()


def epoch(s):
    return int(datetime.fromisoformat(s).timestamp())


@dataclass
class Series:
    t: np.ndarray
    p: np.ndarray  # continuous signal coordinate OHLC
    offset: np.ndarray
    rolls: dict  # index -> simultaneous old/new raw opens and contract names
    manifest: dict
    day_ends: object = None


def assemble(manifest):
    times, prices, offsets, rolls = [], [], [], {}
    offset, count, previous = 0., 0, None
    for rec in manifest['records']:
        file = Path(rec['file'])
        if sha256(file.read_bytes()).hexdigest() != rec['sha256']:
            raise RuntimeError('Changed price input')
        with np.load(file) as z:
            t, p = z['t'].copy(), z['p'].copy()
        if np.any(np.diff(t) <= 0) or not np.isfinite(p).all():
            raise RuntimeError('Invalid timestamps/prices')
        if (p <= 0).any() or np.max(np.abs(p/TICK-np.rint(p/TICK))) > 1e-7:
            raise RuntimeError('Off-grid or nonpositive futures prices')
        if np.any(p[:, 1] < p[:, [0, 3]].max(axis=1)) or np.any(p[:, 2] > p[:, [0, 3]].min(axis=1)):
            raise RuntimeError('Invalid OHLC')
        begin, end = epoch(rec['start']), epoch(rec['end'])
        if previous is not None:
            old_t, old_p, old_name = previous
            a, b = np.searchsorted(old_t, begin), np.searchsorted(t, begin)
            if a == len(old_t) or b == len(t) or old_t[a] != begin or t[b] != begin:
                raise RuntimeError(f'Missing simultaneous roll open: {rec["contract"]}')
            old_open, new_open = float(old_p[a, 0]), float(p[b, 0])
            offset += new_open-old_open
            rolls[count] = dict(time=iso(begin), old=old_name, new=rec['contract'],
                                old_open=old_open, new_open=new_open, offset=offset)
        mask = (t >= begin) & (t < end)
        if not mask.any():
            raise RuntimeError('Empty active contract window')
        times.append(t[mask]); prices.append(p[mask]-offset)
        offsets.append(np.full(mask.sum(), offset)); count += int(mask.sum())
        previous = t, p, rec['contract']
    return Series(np.concatenate(times), np.concatenate(prices), np.concatenate(offsets), rolls, manifest)


def sessions(path, root):
    """Parse events chronologically, not by settlement-date labels.

    Holiday trades can settle on a later day. A subsequent pre-open event is
    an upper bound on an otherwise unreported close, never evidence of trading
    through that boundary. Coverage rejection handles earlier unreported halts.
    Saturday test events are excluded from the production calendar.
    """
    rows = json.loads(Path(path).read_text())
    events = sorted({(epoch(r['timestamp']), r['event']) for r in rows
                     if r['product_code'] == root and r['event'] in ('open', 'resume', 'close', 'halt', 'pre_open')})
    opened, spans = None, []
    for at, kind in events:
        local = datetime.fromtimestamp(at, NY)
        if local.weekday() == 5:
            continue
        if kind in ('open', 'resume'):
            if opened is not None and opened != at:
                raise RuntimeError('Consecutive production opens without a close/pre-open boundary')
            opened = at
        elif opened is not None:
            if at > opened:
                spans.append((opened, at))
            opened = None
    if opened is not None:
        raise RuntimeError('Unclosed calendar interval')
    groups = {}
    for a, b in spans:
        local = datetime.fromtimestamp(a, NY)
        day = (local+timedelta(days=1) if local.hour >= 18 else local).date().isoformat()
        groups.setdefault(day, []).append((a, b))
    return sorted(groups.items())


@dataclass
class Candle:
    start: int
    end: int
    ordinal: int
    o: float
    h: float
    l: float
    c: float


def buckets(calendar, midnight=False):
    for day, spans in calendar:
        first, last = spans[0][0], spans[-1][1]
        boundaries = [first]
        if midnight:
            local = datetime.fromtimestamp(first, NY)
            base = local.replace(hour=0, minute=0, second=0, microsecond=0)
            for k in range(13):
                n = int((base+timedelta(hours=4*k)).timestamp())
                if first < n < last:
                    boundaries.append(n)
        else:
            boundaries.extend(range(first+4*3600, last, 4*3600))
        boundaries.append(last)
        boundaries = sorted(set(boundaries))
        for a, b in zip(boundaries, boundaries[1:]):
            expected = [np.arange(max(a, x), min(b, y), 60, dtype=np.int64)
                        for x, y in spans if max(a, x) < min(b, y)]
            if expected:
                yield a, b, np.concatenate(expected)


def candles(series, calendar, *, midnight=False, strict=False):
    result, stats = [], dict(accepted=0, rejected=0, expected_minutes=0, observed_minutes=0,
                             missing_minutes=0, outside_calendar_minutes=0)
    all_expected = []
    for ordinal, (a, b, expected) in enumerate(buckets(calendar, midnight)):
        if b <= series.t[0] or a > series.t[-1]:
            continue
        all_expected.append(expected)
        left, right = np.searchsorted(series.t, [a, b])
        observed = series.t[left:right]
        valid = np.isin(observed, expected)
        idx = np.arange(left, right)[valid]
        hits = np.isin(expected, observed)
        missing = int((~hits).sum())
        edges = np.diff(np.r_[0, np.flatnonzero(hits)+1, len(hits)+1])-1
        max_missing = int(edges.max(initial=0))
        okay = (len(idx) > 0 and (missing == 0 if strict else
                (len(idx) >= .95*len(expected) and max_missing <= 5)))
        stats['expected_minutes'] += len(expected); stats['observed_minutes'] += len(idx)
        stats['missing_minutes'] += missing
        stats['outside_calendar_minutes'] += int((~valid).sum())
        stats['accepted' if okay else 'rejected'] += 1
        p = series.p[idx]
        result.append(Candle(a, b, ordinal, float(p[0, 0]), float(p[:, 1].max()),
                             float(p[:, 2].min()), float(p[-1, 3])) if okay else None)
    return result, stats, np.concatenate(all_expected)


def signals(candles_, wing=2, bos_state=False):
    """Pivots become knowable only at the right-hand confirming candle close."""
    history, highs, lows, consumed, out = [], [], [], set(), []
    state = 0
    for c in candles_:
        if c is None:
            history, highs, lows, consumed, state = [], [], [], set(), 0
            continue
        history.append(c)
        if len(history) >= 2*wing+1:
            candidate = history[-wing-1]
            neighbors = history[-2*wing-1:-wing-1]+history[-wing:]
            hi, lo = all(candidate.h > x.h for x in neighbors), all(candidate.l < x.l for x in neighbors)
            # Outside candles may be both a strict high and low; both are known now.
            if hi:
                highs.append((candidate.h, candidate.start, c.end))
            if lo:
                lows.append((candidate.l, candidate.start, c.end))
        if len(history) < 2 or not highs or not lows:
            continue
        prev = history[-2].c
        if bos_state:
            side = 1 if prev <= highs[-1][0] and c.c >= highs[-1][0]+TICK else (
                -1 if prev >= lows[-1][0] and c.c <= lows[-1][0]-TICK else 0)
            if not side:
                continue
            pivot = highs[-1] if side == 1 else lows[-1]
            if (side, pivot[1]) in consumed:
                continue
            consumed.add((side, pivot[1]))
            change = state == -side
            state = side
            if not change:
                continue
        else:
            if len(highs) < 2 or len(lows) < 2:
                continue
            up = highs[-1][0] > highs[-2][0] and lows[-1][0] > lows[-2][0]
            down = highs[-1][0] < highs[-2][0] and lows[-1][0] < lows[-2][0]
            side = -1 if up else 1 if down else 0
            if not side:
                continue
            pivot = lows[-1] if side == -1 else highs[-1]
            if (side, pivot[1]) in consumed or side*(prev-pivot[0]) > 1e-8 or side*(c.c-pivot[0]) < TICK-1e-8:
                continue
            consumed.add((side, pivot[1]))
        stop_pivot = highs[-1] if side == -1 else lows[-1]
        out.append(dict(at=c.end, side=side, stop=stop_pivot[0]-side*TICK,
                        broken=pivot[0], pivot_time=pivot[1], pivot_known=pivot[2],
                        stop_pivot_time=stop_pivot[1], stop_pivot_known=stop_pivot[2]))
    return out


def bracket(side, stop, target, prices, slip):
    o, h, l, _ = prices
    if side*(o-stop) <= 0:
        return float(o-side*slip), 'gap_stop', False
    if side*(o-target) >= 0:
        return float(target), 'gap_target', False
    stop_hit = l <= stop if side == 1 else h >= stop
    target_hit = h >= target if side == 1 else l <= target
    if stop_hit:
        return float(stop-side*slip), 'stop', bool(target_hit)
    if target_hit:
        return float(target), 'target', False
    return None


def execute(series, sigs, expected, spec, start, end, *, stress=False, ny_only=False):
    pv, fee = SPECS[spec]
    slip = TICK*(2 if stress else 1)
    fee *= 2 if stress else 1
    orders, rejected = {}, dict(late=0, bad_stop=0, while_open=0, ny_filter=0)
    for s in sigs:
        if not start <= s['at'] < end:
            continue
        if ny_only and not 8 <= datetime.fromtimestamp(s['at'], NY).hour < 16:
            rejected['ny_filter'] += 1
            continue
        idx = int(np.searchsorted(series.t, s['at']))
        if idx == len(series.t) or series.t[idx] >= end:
            continue
        late = np.searchsorted(expected, series.t[idx])-np.searchsorted(expected, s['at'])
        if late > 5:
            rejected['late'] += 1
            continue
        orders[idx] = s
    # Iterate only minutes in evaluation range; all bracket fills still use raw minute paths.
    a, b = np.searchsorted(series.t, [start, end])
    pos, trades, cash, peak, dd = None, [], 0., 0., 0.
    daily, ambiguous = {}, 0
    daily_points = {}
    if series.day_ends is not None:
        for day, finish in series.day_ends:
            idx = min(int(np.searchsorted(series.t, min(finish, end)))-1, int(b)-1)
            if a <= idx < b and finish > start:
                daily_points[idx] = day
    else:
        # Small synthetic fixtures can omit exchange metadata.
        daily_points = {int(b)-1: iso(end)[:10]} if b > a else {}

    def close(i, price, reason, both=False, raw_offset=None):
        nonlocal cash, pos, ambiguous
        gross = pos['side']*(price-pos['entry'])*pv
        costs = 2*fee+pos['roll_cost']
        net = gross-costs
        cash += net
        ambiguous += int(both)
        trades.append({**pos, 'exit_time': iso(series.t[i]+(60 if reason == 'data_end' else 0)), 'exit': price,
                       'exit_raw': price+float(series.offset[i] if raw_offset is None else raw_offset), 'reason': reason,
                       'gross': gross, 'costs': costs, 'fees': 2*fee*(1+pos['rolls']),
                       'net': net, 'net_r': net/(pos['risk_points']*pv), 'ambiguous': both})
        pos = None

    events = np.array(sorted(set(orders) | set(daily_points)), dtype=np.int64)
    i = int(a)
    while i < b:
        if pos is None:
            upcoming = int(np.searchsorted(events, i))
            if upcoming == len(events):
                break
            i = int(events[upcoming])
            if i >= b:
                break
        p = series.p[i]
        if pos is not None and i > 0 and series.t[i]-series.t[i-1] > 60:
            missing = max(0, int(np.searchsorted(expected, series.t[i])-
                                 np.searchsorted(expected, series.t[i-1])-1))
            pos['unobserved_scheduled_minutes'] += missing
            pos['max_unobserved_gap_minutes'] = max(pos['max_unobserved_gap_minutes'], missing)
        # At a roll the adjusted new open is exactly the simultaneous old open.
        # Stop/target gaps close the old contract before any new-contract exposure.
        if pos is not None and i in series.rolls:
            gap = bracket(pos['side'], pos['stop'], pos['target'], (p[0], p[0], p[0], p[0]), slip)
            if gap:
                r = series.rolls[i]
                close(i, *gap, raw_offset=series.offset[i]-(r['new_open']-r['old_open']))
            else:
                pos['rolls'] += 1
                pos['roll_cost'] += 2*fee+2*slip*pv
        if i in orders:
            s = orders[i]
            if pos is not None:
                rejected['while_open'] += 1
            else:
                entry = float(p[0]+s['side']*slip)
                risk = s['side']*(entry-s['stop'])
                if risk < TICK or s['side']*(p[0]-s['stop']) <= 0:
                    rejected['bad_stop'] += 1
                else:
                    pos = dict(side=s['side'], entry_time=iso(series.t[i]), entry_index=i,
                               signal_time=iso(s['at']), entry=entry,
                               entry_raw=entry+float(series.offset[i]), stop=s['stop'],
                               target=entry+s['side']*risk, risk_points=risk,
                               pivot_time=iso(s['pivot_time']), pivot_known=iso(s['pivot_known']),
                               stop_pivot_known=iso(s['stop_pivot_known']), rolls=0, roll_cost=0.,
                               unobserved_scheduled_minutes=0, max_unobserved_gap_minutes=0)
        if pos is not None:
            hit = bracket(pos['side'], pos['stop'], pos['target'], p, slip)
            if hit:
                close(i, *hit)
        if pos is not None and i == b-1:
            close(i, float(p[3]-pos['side']*slip), 'data_end')
        # Conservative intraminute adverse mark before the favorable mark. Include
        # estimated market exit costs. This is a dollar drawdown, not account DD%.
        equity = cash
        if pos is not None:
            equity += pos['side']*(p[3]-pos['entry'])*pv-2*fee-pos['roll_cost']
            adverse = p[2] if pos['side'] == 1 else p[1]
            adverse_equity = cash+pos['side']*(adverse-pos['entry'])*pv-2*fee-pos['roll_cost']-slip*pv
            dd = max(dd, peak-adverse_equity)
        peak = max(peak, equity)
        dd = max(dd, peak-equity)
        if i in daily_points:
            daily[daily_points[i]] = float(equity)
        i += 1
    net = [x['net'] for x in trades]
    profit, loss = sum(max(0, x) for x in net), -sum(min(0, x) for x in net)
    return dict(spec=spec, trades=len(trades), net_pnl=float(sum(net)),
                gross_pnl=float(sum(x['gross'] for x in trades)),
                costs=float(sum(x['costs'] for x in trades)),
                win_rate_pct=100*sum(x > 0 for x in net)/len(net) if net else 0.,
                profit_factor=profit/loss if loss else None, max_drawdown_dollars=float(dd),
                trades_per_trading_day=len(net)/len(daily) if daily else 0.,
                trading_days=len(daily), ambiguous_minutes=ambiguous,
                roll_count=sum(x['rolls'] for x in trades), rejected=rejected,
                average_trade_dollars=float(np.mean(net)) if net else 0.,
                median_stop_dollars=float(np.median([x['risk_points']*pv for x in trades])) if trades else 0.,
                max_stop_dollars=float(max((x['risk_points']*pv for x in trades), default=0.)),
                trades_with_unobserved_minutes=sum(x['unobserved_scheduled_minutes'] > 0 for x in trades),
                max_unobserved_gap_minutes=max((x['max_unobserved_gap_minutes'] for x in trades), default=0),
                net_r=float(sum(x['net_r'] for x in trades)),
                mean_net_r=float(np.mean([x['net_r'] for x in trades])) if trades else 0.,
                journal=trades, daily=daily)


def main(roots=None):
    protocol = json.loads(PROTOCOL.read_text())
    start, split, end = (epoch(protocol[k]) for k in ('evaluation_start', 'split', 'end_exclusive'))
    OUT.mkdir(parents=True, exist_ok=True)
    summaries, coverage, inputs = [], {}, {}
    for root in (roots or protocol['markets']):
        manifest_path = DEST/f'READY_{root}.json'
        manifest = json.loads(manifest_path.read_text())
        if sha256(Path(manifest['schedule_file']).read_bytes()).hexdigest() != manifest['calendar_sha256']:
            raise RuntimeError('Changed calendar input')
        series = assemble(manifest)
        calendar = sessions(manifest['schedule_file'], root)
        series.day_ends = [(day, spans[-1][1]) for day, spans in calendar
                           if spans[0][0] <= series.t[-1] and spans[-1][1] > series.t[0]]
        inputs[root] = manifest
        for variant in protocol['variants']:
            cs, stats, expected = candles(series, calendar, midnight=variant == 'midnight_anchor', strict=variant == 'strict_coverage')
            coverage[root+'_'+variant] = stats
            sigs = signals(cs, wing=3 if variant == 'pivot3' else 5 if variant == 'pivot5' else 2,
                           bos_state=variant == 'bos_state')
            for period, begin, until in (('full', start, end), ('early', start, split), ('recent', split, end)):
                # Each split is a fresh account; structure still warms up on older prices.
                for spec in (root, 'M'+root+'_proxy'):
                    for stress in (False, True):
                        result = execute(series, sigs, expected, spec, begin, until, stress=stress,
                                         ny_only=variant == 'ny_entry_8_16')
                        tag = f'{spec}_{variant}_{period}_{"stress" if stress else "base"}'
                        journal = result.pop('journal'); daily = result.pop('daily')
                        with gzip.open(OUT/(tag+'.json.gz'), 'wt', encoding='utf-8') as stream:
                            json.dump(dict(trades=journal, daily=daily), stream)
                        result.update(root=root, variant=variant, period=period, stress=stress,
                                      journal=tag+'.json.gz', signals=len([s for s in sigs if begin <= s['at'] < until]))
                        summaries.append(result)
            print(json.dumps(dict(root=root, variant=variant, coverage=stats)), flush=True)
    payload = dict(protocol=protocol, protocol_sha256=sha256(PROTOCOL.read_bytes()).hexdigest(),
                   code_sha256={str(p): sha256(p.read_bytes()).hexdigest() for p in (Path(__file__), Path('research/four_hour_download.py'))},
                   inputs=inputs, coverage=coverage, cases=summaries)
    (OUT/'summary.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--roots', nargs='+', choices=['NQ', 'ES'])
    main(parser.parse_args().roots)
