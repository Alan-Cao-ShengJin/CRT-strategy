"""Audit and summarize the fixed five-minute extension; no parameter selection."""
from collections import Counter, defaultdict
import csv
from hashlib import sha256
import json
from math import prod
from pathlib import Path
from datetime import date

from crt.models import INSTRUMENTS, timestamp
from crt.strategy import trading_day, session_at
from crt.config import Config
from research.expanded_markets import register
from research.five_minute_validation import OUT, PROTOCOL, cases, fingerprints, read
from research.five_minute_download import DEST


def monthly(daily):
    groups = defaultdict(list)
    for d in daily:
        groups[d['day'][:7]].append(d['return_pct'])
    return {m: 100*(prod(1+x/100 for x in rs)-1) for m, rs in sorted(groups.items())}


def audit(docs):
    needed = {(t['symbol'], t['contract'], t['entry_time']) for d in docs for t in d['trades']}
    files = {f for d in docs for f in d['input_sha256'] if f.endswith('.csv')}
    raw_opens = {}
    for f in sorted(files):
        with open(f, newline='', encoding='utf-8-sig') as stream:
            for r in csv.DictReader(stream):
                key = (r['symbol'], r['contract'], r['timestamp'])
                if key in needed:
                    value = float(r['open'])
                    assert key not in raw_opens or raw_opens[key] == value
                    raw_opens[key] = value
    count = 0
    for d in docs:
        c = d['case']; fee = 1.50 if c['stress'] else .85; slip = 2 if c['stress'] else 1
        allocations = {(a['symbol'], a['time']): a for a in d['allocations']}
        byday, bysession = Counter(), Counter()
        configs = {s: Config(symbols=[s], sessions={'new_york': ['09:30', '12:00'], 'asia': ['20:00', '22:00']}) for s in c['basket']}
        for a in d['allocations']:
            assert 0 < a['risk'] <= .01*a['equity_before']+1e-6
            assert a['total_risk'] <= .02*a['equity_before']+1e-6
            assert a['reserved'] <= .8*a['equity_before']+1e-6
            assert a['positions'] <= 2
        for t in d['trades']:
            inst = INSTRUMENTS[t['symbol']]; meta = d['setups'][t['symbol']][t['setup_id']]
            assert t['qty'] > 0 and int(t['qty']) == t['qty']
            raw = raw_opens[(t['symbol'], t['contract'], t['entry_time'])]
            assert abs(t['entry']-raw-t['side']*slip*inst.tick) < 1e-7
            assert abs(t['fees']-2*fee*t['qty']) < 1e-7
            assert abs(t['net']-((t['exit']-t['entry'])*t['side']*inst.point_value*t['qty']-t['fees'])) < 1e-6
            stop_fill = t['stop']-t['side']*slip*inst.tick
            planned = (abs(t['entry']-stop_fill)*inst.point_value+2*fee)*t['qty']
            assert abs(t['risk']-planned) < 1e-6
            assert abs(allocations[(t['symbol'], t['entry_time'])]['risk']-planned) < 1e-6
            assert timestamp(meta['broken_pivot_time']) < timestamp(meta['pivot_known_at']) <= timestamp(meta['change_confirmed']) < timestamp(t['entry_time'])
            assert meta['entry_confirmation'] == t['entry_time']
            delay = (timestamp(t['entry_time'])-timestamp(meta['change_confirmed'])).total_seconds()/60
            assert 5 <= delay <= 15 and t['stop'] == meta['structural_stop']
            distance = t['side']*(t['entry']-t['stop']); reward = t['side']*(t['target']-t['entry'])
            assert distance > 0 and 2*distance-1e-7 <= reward < 2*distance+inst.tick+1e-7
            day = trading_day(timestamp(t['entry_time']))
            assert c['start'] <= day < c['end']
            byday[(t['symbol'], day)] += 1
            session = session_at(timestamp(t['entry_time']), configs[t['symbol']])
            assert session is not None
            bysession[(t['symbol'], session)] += 1
            count += 1
        assert max(byday.values(), default=0) <= 2 and max(bysession.values(), default=0) <= 1
        pnl = sum(t['net'] for t in d['trades'])
        assert abs(pnl-d['stats']['net_pnl']) < 1e-6
        assert abs(d['daily'][-1]['equity']-c['capital']-pnl) < 1e-6
        assert abs(sum(x['pnl'] for x in d['daily'])-pnl) < 1e-6
        assert abs(100*(prod(1+x['return_pct']/100 for x in d['daily'])-1)-d['stats']['return_pct']) < 1e-6
    return dict(status='passed', cases=len(docs), simulated_trade_records=count,
                unique_entry_opens=len(raw_opens), controls=[dict(tag=d['case']['tag'], **d['control']) for d in docs if 'control' in d])


def fmt(x, digits=2):
    return '-' if x is None else f'{x:.{digits}f}'


def report(docs, checks):
    def get(period, basket, capital=50000, stress=False):
        return [d for d in docs if (d['case']['period'], d['case']['name'], d['case']['capital'], d['case']['stress']) == (period, basket, capital, stress)]

    def lower(rs, key):
        return min(r['stats'][key] for r in rs)

    def higher(rs, key):
        return max(r['stats'][key] for r in rs)

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True, layout='constrained')
    for name, color in [('MCL', '#177a65'), ('MNQ', '#4169b1'), ('combined', '#c37920')]:
        d = next(x for x in get('extended', name) if x['case']['path'] == 'high_first')
        days = [date.fromisoformat(x['day']) for x in d['daily']]
        equity = [x['equity'] for x in d['daily']]
        peak, dd = 50000., []
        for value in equity:
            peak = max(peak, value); dd.append(100*(value/peak-1))
        axes[0].plot(days, [100*(x/50000-1) for x in equity], label=name, color=color)
        axes[1].plot(days, dd, label=name, color=color)
    for ax in axes:
        ax.axvline(date(2025, 11, 3), color='#777777', linestyle='--', linewidth=1)
        ax.axhline(0, color='#aaaaaa', linewidth=.7); ax.grid(alpha=.2)
    axes[0].set_title('Unchanged 5-minute CHoCH: earlier losses, later recovery')
    axes[0].set_ylabel('Account return (%)'); axes[0].legend(loc='upper left')
    axes[1].set_ylabel('Day-end drawdown (%)')
    axes[1].set_xlabel('Dashed line: start of the previously inspected history')
    fig.savefig(OUT/'extended_equity.png', dpi=160); plt.close(fig)

    rows = ['# Five-minute CHoCH: additional-history validation', '',
        f"**The added earlier period weakens the previous profitability assessment. At $50,000, MCL returned {lower(get('earlier','MCL'),'return_pct'):+.2f}%, MNQ {lower(get('earlier','MNQ'),'return_pct'):+.2f}%, and the combined account {lower(get('earlier','combined'),'return_pct'):+.2f}%. Positive extended-period returns do not remove that losing regime.**", '',
        'This study keeps the existing **five-minute confirmed-retest / 2R** rules fixed and tests MCL, MNQ and a shared account trading both. '
        'It adds **7 April-31 October 2025**, before the previously studied year, and also replays the uninterrupted extended history through **4 November 2026**. '
        'Dates are CME trading dates, with the end exclusive at 5 November 2026. No new filter, stop, target, session or market ranking is fitted.', '',
        '**This is retrospective reverse-time validation.** The markets were chosen using later results, and the four-hour study examined overlapping history. '
        'It is additional evidence, not an untouched forward holdout. The two OHLC paths are assumptions, not observed tick paths or confidence bounds.', '',
        '## New earlier-period result', '',
        'Fresh $50,000 accounts; 1% maximum planned risk per entry, whole contracts, fees/slippage, capital reservations and shared-account risk limits. '
        'Rows show lower return, trade count, PF and net R, and higher drawdown across the two minute-price paths.', '',
        '| Enabled markets | Trades | Win rate | Return | Mean daily return | Trades/day | Minute-mark DD | PF | Net R | Higher-cost resimulation return |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for name in ('MCL', 'MNQ', 'combined'):
        rs, st = get('earlier', name), get('earlier', name, stress=True)
        rows.append(f"| {name} | {lower(rs,'trades')} | {fmt(lower(rs,'win_rate_pct'))}% | {lower(rs,'return_pct'):+.2f}% | {lower(rs,'mean_daily_return_pct'):+.3f}% | {lower(rs,'mean_trades_per_market_day'):.2f} | {higher(rs,'max_minute_mark_drawdown_pct'):.2f}% | {fmt(lower(rs,'profit_factor'))} | {lower(rs,'net_r'):+.2f} | {lower(st,'return_pct'):+.2f}% |")
    mcl_positive = all(d['stats']['net_pnl'] > 0 for d in get('earlier', 'MCL'))
    mcl_stress = all(d['stats']['net_pnl'] > 0 for d in get('earlier', 'MCL', stress=True))
    mnq_positive = all(d['stats']['net_pnl'] > 0 for d in get('earlier', 'MNQ'))
    rows += ['', f'MCL profitable in both paths on the added earlier history: **{"yes" if mcl_positive else "no"}**; '
             f'MCL profitable in both full higher-cost resimulations: **{"yes" if mcl_stress else "no"}**. '
             f'MNQ profitable in both paths on the added earlier history: **{"yes" if mnq_positive else "no"}**. '
             'These observations do not establish a persistent future edge.', '',
        'The combined basket can have two positions and more deployed risk than either single-market account. '
        'It is an allocation experiment, not a comparison at matched realized volatility or identical average exposure.', '',
        '## Uninterrupted extended history', '',
        'Each row starts with $50,000 on 7 April 2025 and compounds through 4 November 2026. '
        'This is a fresh full replay, not the sum of separately funded period returns.', '',
        '| Enabled markets | Trades | Period return | Mean daily return | Trades/day | Minute-mark DD | PF | P&L minus five largest winning trades |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for name in ('MCL', 'MNQ', 'combined'):
        rs = get('extended', name)
        rows.append(f"| {name} | {lower(rs,'trades')} | {lower(rs,'return_pct'):+.2f}% | {lower(rs,'mean_daily_return_pct'):+.3f}% | {lower(rs,'mean_trades_per_market_day'):.2f} | {higher(rs,'max_minute_mark_drawdown_pct'):.2f}% | {fmt(lower(rs,'profit_factor'))} | ${lower(rs,'without_best_five'):,.2f} |")
    rows += ['', 'Removing the five best trade profits is a concentration diagnostic at the recorded quantities; '
             'it does not resimulate later account sizing, risk halts or returns. Extended-period results include the previously inspected discovery period and therefore are not independent validation.', '',
        '![Extended account returns and daily drawdown](research/results/five_minute_validation/extended_equity.png)', '',
        'Chart uses day-end marks and the high-first path. The table reports the larger minute-mark drawdown, which can exceed the plotted daily drawdown.', '',
        '## Smaller accounts on the earlier period', '',
        '| Enabled markets | Starting equity | Trades | Return | Minute-mark DD | Capital/risk rejections |',
        '|---|---:|---:|---:|---:|---:|']
    for capital in (1000, 10000, 50000):
        for name in ('MCL', 'MNQ', 'combined'):
            rs = get('earlier', name, capital)
            rejections = max(d['rejections'].get('capital_or_risk_below_one_unit', 0) for d in rs)
            rows.append(f"| {name} | ${capital:,} | {lower(rs,'trades')} | {lower(rs,'return_pct'):+.2f}% | {higher(rs,'max_minute_mark_drawdown_pct'):.2f}% | {rejections} |")
    rows += ['', 'The inherited static full-initial-margin proxies are $884.53 per MCL and $4,647.32 per MNQ, with at most 80% of account equity allocated on entry. '
             '**A $1,000 account cannot open either contract under these assumptions.** Its 0% result reflects no trades, not a safe profitable strategy. '
             'These are the previous study’s planning values, not current broker quotations or reconstructed 2025 margin requirements. '
             'The simulator excludes platform/data subscriptions, taxes, withdrawals, slippage beyond its assumptions and broker-specific buying power.', '',
        '## Monthly extended-account returns', '',
        'Monthly returns compound daily account marks. Values below use the high-first path; all low-first daily marks and journals are also retained.', '',
        '| Month | MCL | MNQ | Combined |', '|---|---:|---:|---:|']
    months = {n: monthly(next(d for d in get('extended', n) if d['case']['path'] == 'high_first')['daily']) for n in ('MCL', 'MNQ', 'combined')}
    for m in sorted(set().union(*(x for x in months.values()))):
        rows.append('| '+m+' | '+' | '.join(f'{months[n].get(m,0):+.2f}%' for n in ('MCL', 'MNQ', 'combined'))+' |')
    rows += ['', '## Session and direction diagnostics', '',
        'Descriptive breakdown of the uninterrupted $50,000 single-market high-first replays. '
        'This breakdown was added after seeing the main period results; it is not a tested filter or a selection rule. '
        'Net R uses each trade’s actual planned risk including modeled stop slippage and round-trip fees.', '',
        '| Market | Entry period | Session | Side | Trades | Wins | Net R |',
        '|---|---|---|---|---:|---:|---:|']
    for name in ('MCL', 'MNQ'):
        d = next(x for x in get('extended', name) if x['case']['path'] == 'high_first')
        buckets = defaultdict(list)
        cfg = Config(symbols=[name])
        for t in d['trades']:
            when = timestamp(t['entry_time'])
            period = 'added earlier' if trading_day(when) < '2025-11-03' else 'previously inspected'
            session = session_at(when, cfg).split(':')[-1]
            buckets[(period, session, 'long' if t['side'] == 1 else 'short')].append(t)
        for (period, session, side), ts in sorted(buckets.items()):
            rows.append(f"| {name} | {period} | {session} | {side} | {len(ts)} | {sum(t['net']>0 for t in ts)} | {sum(t['net']/t['risk'] for t in ts):+.2f} |")
    rows += ['', '## Cost and execution assumptions', '',
        'Base: $0.85 commission per contract per side and one adverse tick on market fills. '
        'The six earlier $50,000 stress cases **fully rerun** at $1.50 per side and two adverse ticks, including new entry prices, targets, sizes, fills and risk halts. '
        'This differs from the prior report’s fixed-fill repricing, which is retained separately in each baseline result for reference. Neither is an actual broker fee quote or measured order-book slippage.', '',
        'Risk and capital limits remain: 1% maximum planned equity risk per entry, 2% total planned open risk, two simultaneous positions, one equity-index future, '
        'a 3% daily-loss budget and at most 80% capital utilization. Entry risk includes modeled stop slippage and round-trip fees. Gaps can exceed planned losses. '
        'Five-minute rules remain two-left/two-right confirmed pivots, structural stop, retest confirmation within three candles, immediate next-minute execution, '
        'first eligible setup per NY/Asia session, two trades per asset per CME day, original missing-bucket resets and daily flattening.', '',
        '## Reproduction and data audit', '',
        f'**{checks["cases"]} cases / {checks["simulated_trade_records"]:,} simulated trade records / {checks["unique_entry_opens"]:,} distinct entry opens** audited. '
        'Overlapping paths, capitals and periods are not independent trade samples. Audit checks raw entry prices, commissions, 2R brackets, causal pivot/retest times, '
        'whole-contract sizing, shared risk/capital ceilings, per-session/day limits and daily/final equity reconciliation. '
        'Both saved MCL controls reproduce every trade and daily equity exactly; both MNQ controls match fresh original-engine replays.', '',
        'All raw data use actual dated MCL/MNQ contracts, not standard-contract proxies. MCL expiration dates and tick sizes come from '
        '[Massive historical contract references](https://massive.com/docs/rest/futures/contracts). '
        'Calendar rolls retain the original study’s convention, not a hindsight volume choice. '
        '[CME describes MCL as a 100-barrel contract](https://www.cmegroup.com/markets/energy/crude-oil/micro-wti-crude-oil.contractSpecs.html). '
        'Raw minute data, calendars and source/input hashes are preserved locally. Missing bars are not fabricated; conflicting duplicate minutes are quarantined. '
        'The inherited calendar parser and reset convention are preserved for comparability, including their limitations around holidays and missing sessions. '
        'Tagged stale old-contract marks, if present, are unverified exit assumptions.', '',
        '| Case group | Maximum tagged roll marks | Largest return difference between paths |', '|---|---:|---:|']
    for period in ('earlier', 'extended', 'control'):
        rs = [d for d in docs if d['case']['period'] == period]
        groups = defaultdict(list)
        for d in rs:
            c = d['case']; groups[(c['name'], c['capital'], c['stress'])].append(d)
        difference = max((max(x['stats']['return_pct'] for x in g)-min(x['stats']['return_pct'] for x in g) for g in groups.values()), default=0)
        rows.append(f"| {period} | {max(len(d['roll_marks']) for d in rs)} | {difference:.4f} percentage points |")
    tagged = [d for d in docs if d['case']['period'] == 'extended' and d['case']['path'] == 'high_first' and d['roll_marks']]
    if tagged:
        rows += ['', 'The extended MNQ and combined replays each contain one MNQ exit booked on 13 September 2026 using the last available old-contract mark from 11 September. '
                 'The recorded net P&L on that trade is -$180.70 for the standalone MNQ account and -$361.40 for the combined account. '
                 'These are tagged research assumptions, not verified market exits; the extended totals inherit this uncertainty. '
                 '**The added earlier-period cases and all MCL-only cases have no tagged roll exits.**']
    rows += ['', 'A profitable earlier-period result increases the case for paper testing unchanged rules. A poor result calls for revising the evidence assessment before searching for a new filter. '
             'Neither outcome authorizes live trading or a claim of dependable daily income. No existing paper configuration was changed.', '',
             'Reproduce: `python -m research.five_minute_download`, `python -m research.five_minute_validation --workers 3`, '
             '`python -m research.five_minute_validation_report`. All 34 cases, including small accounts, losing results and stress cases, are saved in '
             '`research/results/five_minute_validation/`. The frozen design is `research/five_minute_validation_protocol.json`.', '']
    Path('CHOCH_FIVE_MINUTE_VALIDATION.md').write_text('\n'.join(rows), encoding='utf-8')


def main():
    register()
    policy = json.loads(PROTOCOL.read_text()); declared = cases(policy)
    docs = [read(OUT/(c['tag']+'.json.gz')) for c in declared]
    code = fingerprints(); digest = sha256(PROTOCOL.read_bytes()).hexdigest()
    for d in docs:
        assert d['source_sha256'] == code and d['protocol_sha256'] == digest
    inputs = {f: h for d in docs for f, h in d['input_sha256'].items()}
    for f, h in inputs.items():
        assert sha256(Path(f).read_bytes()).hexdigest() == h
    checks = audit(docs)
    checks['report_source_sha256'] = sha256(Path(__file__).read_bytes()).hexdigest()
    checks['test_source_sha256'] = sha256(Path('tests/test_five_minute_validation.py').read_bytes()).hexdigest()
    checks['journal_sha256'] = {str(OUT/(c['tag']+'.json.gz')): sha256((OUT/(c['tag']+'.json.gz')).read_bytes()).hexdigest() for c in declared}
    (OUT/'audit.json').write_text(json.dumps(checks, indent=2), encoding='utf-8')
    summary = dict(protocol=policy, protocol_sha256=digest, source_sha256=code, input_sha256=inputs,
                   cases=[dict(case=d['case'], stats=d['stats'], rejections=d['rejections'],
                               roll_marks=d['roll_marks'], monthly_returns=monthly(d['daily'])) for d in docs], audit=checks)
    (OUT/'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    report(docs, checks)
    print(json.dumps({k: checks[k] for k in ('status', 'cases', 'simulated_trade_records', 'unique_entry_opens')}), flush=True)


if __name__ == '__main__':
    main()
