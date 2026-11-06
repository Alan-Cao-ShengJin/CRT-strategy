"""Audit all four-hour journals and publish the bounded comparison."""
from hashlib import sha256
import gzip
import json
from pathlib import Path

import numpy as np

from research.four_hour import OUT, SPECS, TICK, assemble, epoch, sessions, candles


def audit(doc):
    series = {root: assemble(m) for root, m in doc['inputs'].items()}
    count, hashes = 0, {}
    for root, s in series.items():
        m = doc['inputs'][root]
        assert sha256(Path(m['schedule_file']).read_bytes()).hexdigest() == m['calendar_sha256']
        _, _, expected = candles(s, sessions(m['schedule_file'], root))
        t = s.t[(s.t >= epoch(doc['protocol']['evaluation_start'])) & (s.t < epoch(doc['protocol']['end_exclusive']))]
        assert np.isin(t, expected).all(), 'Observed evaluation prices outside exchange calendar'
    for case in doc['cases']:
        file = OUT/case['journal']
        hashes[str(file)] = sha256(file.read_bytes()).hexdigest()
        j = json.loads(gzip.decompress(file.read_bytes()))
        s = series[case['root']]
        pv, fee = SPECS[case['spec']]
        slip = TICK*(2 if case['stress'] else 1)
        fee *= 2 if case['stress'] else 1
        previous_exit = None
        for t in j['trades']:
            i = t['entry_index']
            assert epoch(t['entry_time']) == s.t[i]
            assert abs(t['entry']-s.p[i, 0]-t['side']*slip) < 1e-7
            assert abs(t['entry_raw']-s.p[i, 0]-s.offset[i]-t['side']*slip) < 1e-7
            assert epoch(t['pivot_known']) <= epoch(t['signal_time']) <= epoch(t['entry_time'])
            assert epoch(t['stop_pivot_known']) <= epoch(t['signal_time'])
            assert t['risk_points'] > 0
            assert abs(t['target']-t['entry']-t['side']*t['risk_points']) < 1e-7
            assert abs(t['stop']-t['entry']+t['side']*t['risk_points']) < 1e-7
            assert abs(t['gross']-t['side']*(t['exit']-t['entry'])*pv) < 1e-7
            assert abs(t['costs']-(2*fee+t['rolls']*(2*fee+2*slip*pv))) < 1e-7
            assert abs(t['fees']-2*fee*(1+t['rolls'])) < 1e-7
            assert abs(t['net']-(t['gross']-t['costs'])) < 1e-7
            assert abs(t['net_r']-t['net']/(t['risk_points']*pv)) < 1e-7
            if previous_exit is not None:
                assert epoch(t['entry_time']) >= previous_exit
            previous_exit = epoch(t['exit_time'])
            count += 1
        assert len(j['trades']) == case['trades']
        assert abs(sum(t['net'] for t in j['trades'])-case['net_pnl']) < 1e-7
        assert abs(sum(t['net_r'] for t in j['trades'])-case['net_r']) < 1e-7
        if j['daily']:
            assert abs(list(j['daily'].values())[-1]-case['net_pnl']) < 1e-7
    return dict(cases=len(doc['cases']), simulated_trade_records=count, journal_sha256=hashes)


def main():
    path = OUT/'summary.json'
    doc = json.loads(path.read_text())
    assert len(doc['cases']) == 168, 'Both instruments and every declared case must finish'
    assert sha256(Path('research/four_hour_protocol.json').read_bytes()).hexdigest() == doc['protocol_sha256']
    for file, digest in doc['code_sha256'].items():
        assert sha256(Path(file).read_bytes()).hexdigest() == digest, 'Changed simulation source'
    checks = audit(doc)
    checks['report_source_sha256'] = sha256(Path(__file__).read_bytes()).hexdigest()
    checks['test_source_sha256'] = sha256(Path('tests/test_four_hour.py').read_bytes()).hexdigest()
    (OUT/'audit.json').write_text(json.dumps(checks, indent=2), encoding='utf-8')
    rows = doc['cases']

    def get(spec, variant='primary', period='full', stress=False):
        return next(x for x in rows if (x['spec'], x['variant'], x['period'], x['stress']) == (spec, variant, period, stress))

    text = [
        '# Independent check of the video’s four-hour CHoCH strategy', '',
        '**The primary reconstruction lost money on both NQ and ES. None of the seven declared interpretations maintained positive dollar profit and net R across both markets, time segments and cost assumptions. The earlier MCL five-minute retest remains the stronger paper-validation candidate.**', '',
        'The video describes a different setup from our earlier five-minute retest study: confirmed four-hour change of character, immediate entry, a swing stop, a 1R target, and no required liquidity sweep. '
        'Its reported standard-contract profits are not account return percentages. Source: [AI Pathways video](https://www.youtube.com/watch?v=z97PnFIPoFg), particularly 11:46–13:15 and 14:31–14:50. '
        'The transcript does not fix pivot width, initial trend state, candle anchor, roll procedure or complete executable rules. This is an independent mechanical reconstruction, not verification of the presenter’s code or claimed results.', '',
        'Evaluation: **7 April 2025 00:00 UTC to 5 November 2026 00:00 UTC**, split at **3 November 2025 00:00 UTC**. '
        'Available minute prices begin in November 2024, but the usable common calendar starts in March 2025; April allows warmup. '
        'The earlier segment was unexamined locally before this study. Both segments are historical, overlap a period used by the video, and are not pristine forward validation.', '',
        '## Primary reconstruction', '',
        'Strict two-left/two-right confirmed swing highs and lows establish higher-high/higher-low or lower-high/lower-low structure. '
        'Take the first opposite closing break by a tick; enter the next available minute open; stop a tick beyond the latest confirmed opposite swing and target 1R from the slipped entry. '
        'One position per instrument; positions may stay overnight. Four-hour candles start at the exchange session open, normally 18:00 New York, with a final shorter candle. '
        'A stop and target both touched in one minute count as a stop. Actual price gaps fill at the observed open with adverse slippage.', '',
        '| Market | Trades | Win rate | Net profit, one standard contract | Total net R | PF | Dollar drawdown | Higher-cost net profit |',
        '|---|---:|---:|---:|---:|---:|---:|---:|'
    ]
    for root in ('NQ', 'ES'):
        r, stressed = get(root), get(root, stress=True)
        text.append(f"| {root} | {r['trades']} | {r['win_rate_pct']:.1f}% | ${r['net_pnl']:,.0f} | {r['net_r']:+.2f} | {r['profit_factor']:.2f} | ${r['max_drawdown_dollars']:,.0f} | ${stressed['net_pnl']:,.0f} |")
    text += ['', 'R is trade net P&L divided by its initial stop risk in dollars. Summing R makes different stop sizes easier to compare; it is not a compounding, integer-contract account return. '
             'Dollar drawdown uses minute closes plus adverse minute extremes while positions remain open, with estimated exit costs. It does not reconstruct a tick-by-tick peak-to-trough path or broker liquidation. '
             'The fixed-contract study does not enforce margin, an account loss floor, or a 1% risk ceiling.', '',
             '## All predeclared interpretations', '',
             'Each row changes one primary rule. No target or stop optimization was added. Every interpretation is disclosed, including losses.', '',
             '| Interpretation | NQ trades | NQ net $ | NQ net R | ES trades | ES net $ | ES net R |',
             '|---|---:|---:|---:|---:|---:|---:|']
    for v in doc['protocol']['variants']:
        a, b = get('NQ', v), get('ES', v)
        text.append(f"| {v} | {a['trades']} | {a['net_pnl']:,.0f} | {a['net_r']:+.2f} | {b['trades']} | {b['net_pnl']:,.0f} | {b['net_r']:+.2f} |")
    both = [v for v in doc['protocol']['variants'] if all(get(s, v)['net_pnl'] > 0 for s in ('NQ', 'ES'))]
    robust = [v for v in doc['protocol']['variants'] if all(get(s, v, p, st)['net_pnl'] > 0 and get(s, v, p, st)['net_r'] > 0
              for s in ('NQ', 'ES') for p in ('early', 'recent') for st in (False, True))]
    text += ['', f'Positive dollar profit in both markets over the full period: **{len(both)}/7** ({", ".join(both) or "none"}). '
             f'Positive dollar profit **and** net R in both time segments, both markets and both cost assumptions: **{len(robust)}/7** ({", ".join(robust) or "none"}). '
             'These overlapping interpretations are correlated sensitivity checks, not independent confirmations or a statistical significance test.', '',
             '| Interpretation | Market | Early net $ | Early net R | Recent net $ | Recent net R |',
             '|---|---|---:|---:|---:|---:|']
    for v in doc['protocol']['variants']:
        for s in ('NQ', 'ES'):
            a, b = get(s, v, 'early'), get(s, v, 'recent')
            text.append(f"| {v} | {s} | {a['net_pnl']:,.0f} | {a['net_r']:+.2f} | {b['net_pnl']:,.0f} | {b['net_r']:+.2f} |")
    text += ['', 'Each segment starts flat and closes residual positions at its final available close; split results need not sum to the uninterrupted full-period result.', '',
             '## Micro sizing and execution limits', '',
             'MNQ/MES figures below are **price-path proxies using standard NQ/ES bars**, with micro multipliers and micro fees. '
             'They are not backtests of actual micro fills. No account return or required account balance is established by these fixed-size results.', '',
             '| Primary proxy | Net profit, one micro | Median stop risk, one micro | Largest stop risk | Trades/day |',
             '|---|---:|---:|---:|---:|']
    for root in ('MNQ_proxy', 'MES_proxy'):
        r = get(root)
        text.append(f"| {root} | ${r['net_pnl']:,.2f} | ${r['median_stop_dollars']:,.2f} | ${r['max_stop_dollars']:,.2f} | {r['trades_per_trading_day']:.3f} |")
    text += ['', 'At a 1% risk ceiling, one micro contract needs account equity at least **100 times the trade’s dollar stop risk**, before margin and gap risk. '
             'A small account must skip signals whose minimum contract size exceeds that budget. The video’s standard-contract dollar profits cannot be scaled into a small account by assuming fractional futures contracts.', '',
             'Assumed costs: standard $2.50/side or micro $0.85/side, one adverse tick on market entries, stop/data-end exits, and both roll legs. '
             'Stress doubles commissions and uses two adverse ticks; all executions rerun. These are modeling assumptions, not broker fee quotations. '
             'The gross P&L field already includes entry/exit market slippage; the costs field contains commissions plus roll slippage.', '',
             '## Data and audit', '',
             'Dated quarterly contracts switch at 16:00 New York on the Thursday eight calendar days before third Friday. '
             'Simultaneous old/new opens establish a causal spread offset for signal coordinates. Open positions pay for both roll legs. '
             'No artificial spread jump is booked as profit. Require at least 95% observed scheduled minutes and no missing scheduled run longer than five minutes for a signal candle; strict-coverage sensitivity requires every minute. '
             'Rejected buckets reset structure; no OHLC is invented. A missing bar can mean no trades or incomplete history, which this feed alone cannot distinguish.', '',
             'Parse calendar events chronologically: holiday settlement labels may combine multiple sessions. A later pre-open bounds an omitted close; it does not prove the market remained tradable up to that time. '
             'Earlier unreported halts produce missing-minute rejection. Saturday testing events are excluded. Some overnight outage periods and sparse new-contract roll minutes therefore remain coverage limitations. '
             'One conflicting ES minute in January 2025 was quarantined rather than choosing a duplicate; it precedes evaluation.', '',
             '| Market | Primary accepted/rejected buckets, including available warmup | Trades with absent scheduled minutes while held | Largest such gap, minutes |',
             '|---|---:|---:|---:|']
    for s in ('NQ', 'ES'):
        c, r = doc['coverage'][s+'_primary'], get(s)
        text.append(f"| {s} | {c['accepted']} / {c['rejected']} | {r['trades_with_unobserved_minutes']} | {r['max_unobserved_gap_minutes']} |")
    text += ['', 'Calendar coverage counts differ before the common evaluation window. There are no observed evaluation-period price minutes outside the parsed calendars in the acquired snapshots. '
             'Orders through gaps execute only at the next observed price; unobserved trading paths are not demonstrated fills.', '',
             f"Audit: **{checks['cases']} cases / {checks['simulated_trade_records']:,} simulated trade records** reconciled against cached entry opens, causal pivot times, 1R brackets, roll charges, fees, net R and final equity. "
             'These records include overlapping periods, sensitivities and proxies, not that many independent trades. '
             'The unit tests cover pivot confirmation, prefix causality, DST and final shorter candles, scheduled halts, holiday labels, unexpected gaps, stop-first fills and roll continuity.', '',
             'Primary references: [Massive aggregate semantics](https://massive.com/docs/rest/futures/aggregates), '
             '[CME NQ specifications](https://www.cmegroup.com/markets/equities/nasdaq/e-mini-nasdaq-100.contractSpecs.html), '
             '[CME ES specifications](https://www.cmegroup.com/markets/equities/sp/e-mini-sandp500.contractSpecs.html), '
             '[CME trading hours and Saturday testing calendar](https://www.cmegroup.com/trading-hours.html).', '',
             '## Where the stronger evidence remains', '',
             'The existing **MCL five-minute confirmed-retest / 2R** candidate remains a separate strategy. '
             'Its same-date May–November check on a $50,000 account at 1% planned risk returned **+25.18%**, with **59 trades**, **52.54% wins**, **PF 2.02**, **4.90% minute-mark drawdown**, '
             'and **+$11,277** under fixed-fill higher-cost repricing. Mean daily return was **0.171%** across 134 dates; it did not trade every day. '
             'See [65-market comparison](CHOCH_UNIVERSE65.md) and [training-based selection](CHOCH_POOL_SEARCH.md). '
             'That candidate came from a broad historical search, so selection bias and dependence on one market regime remain. It is a paper-validation candidate, not established future income.', '',
             'Adding all 65 markets produced only **+3.82%**, with **19.56% drawdown** and negative stressed P&L over the same dates. '
             'The evidence favors testing a narrow candidate rather than assuming more assets or more trades will improve returns. '
             'None of these studies supports a dependable single-digit percentage return every day. Do not replace the paper watchlist with the best-looking four-hour variant after this retrospective comparison.', '',
             'Reproduce: `python -m research.four_hour_download`, `python -m research.four_hour`, `python -m research.four_hour_report`. '
             'Install the repository research/test extras first. Raw vendor data and the full source transcript stay in ignored local directories. '
             'Protocol, source hashes, input hashes, all summaries, audited compressed journals and the audit are retained.', '']
    Path('CHOCH_FOUR_HOUR.md').write_text('\n'.join(text), encoding='utf-8')
    print(json.dumps(dict(audit_cases=checks['cases'], audited_records=checks['simulated_trade_records'],
                          both_positive=both, stable_both_segments_markets_costs=robust)), flush=True)


if __name__ == '__main__':
    main()
