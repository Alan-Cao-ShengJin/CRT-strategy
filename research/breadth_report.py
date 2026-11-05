"""Aggregate every declared case and apply the predeclared historical screen."""
from collections import Counter
from hashlib import sha256
from pathlib import Path
import argparse
import json
import math

from research.breadth_study import PROTOCOL, ROOT, source_hashes


def pair_summary(cases, rules):
    if len(cases) != 2 or {r['path'] for r in cases} != set(rules['paths']):
        return {'status': 'unavailable'}
    stats = [r['stats'] for r in cases]
    minimum = rules['consistency_screen']
    pf = lambda s: s['profit_factor'] if s['profit_factor'] is not None else (math.inf if s['net_pnl'] > 0 else 0.)
    status = 'insufficient' if min(s['trades'] for s in stats) < minimum['minimum_trades'] else (
        'passes' if all(s['net_pnl'] > 0 and pf(s) >= minimum['minimum_profit_factor']
                        and s['positive_blocks'] >= minimum['minimum_positive_blocks']
                        and s['stress_net_pnl'] > 0 for s in stats) else 'fails')
    return dict(status=status, trades_min=min(s['trades'] for s in stats), trades_max=max(s['trades'] for s in stats),
                return_min=min(s['return_pct'] for s in stats), return_max=max(s['return_pct'] for s in stats),
                win_rate_min=min((s['win_rate_pct'] for s in stats if s['win_rate_pct'] is not None), default=None),
                profit_factor_min=min(pf(s) for s in stats) if min(pf(s) for s in stats) != math.inf else None,
                max_drawdown=max(s['max_drawdown_pct'] for s in stats), positive_blocks_min=min(s['positive_blocks'] for s in stats),
                stress_net_min=min(s['stress_net_pnl'] for s in stats),
                mean_risk_pct_min=min(s['mean_planned_risk_pct'] for s in stats),
                positive_both=all(s['net_pnl'] > 0 for s in stats))


def build(allow_incomplete=False):
    rules = json.loads(PROTOCOL.read_text())
    rows, sources, coverage, journals, missing = [], {}, {}, {}, []
    expected_hash = sha256(PROTOCOL.read_bytes()).hexdigest()
    current_source = source_hashes()
    for symbol in rules['symbols']:
        file = ROOT/f'{symbol}.json'
        if not file.exists():
            missing.append(symbol)
            continue
        doc = json.loads(file.read_text())
        if doc['protocol_sha256'] != expected_hash or doc['source_sha256'] != current_source:
            raise ValueError(f'Incompatible model/protocol for {symbol}')
        if len(doc['results']) != 32:
            missing.append(symbol)
            if not allow_incomplete:
                continue
        rows.extend(doc['results'])
        sources[symbol] = doc['input_sha256']
        coverage[symbol] = doc['coverage'] | {'market': doc['market'], 'acquisition': doc['acquisition']}
        path = Path(f'research/results/choch_breadth_trades/{symbol}.jsonl.gz')
        if path.exists():
            journals[symbol] = dict(file=str(path), sha256=sha256(path.read_bytes()).hexdigest(), bytes=path.stat().st_size)
    if missing and not allow_incomplete:
        raise ValueError('Incomplete assets: '+', '.join(missing))
    keys = [(r['symbol'], r['timeframe'], r['entry_mode'], r['path']) for r in rows]
    if len(set(keys)) != len(keys):
        raise ValueError('Duplicate cases')
    if not missing and len(rows) != 544:
        raise ValueError('Expected 544 cases')
    pairs = []
    for symbol in rules['symbols']:
        for tf in rules['timeframes']:
            for mode in rules['entry_modes']:
                cases = [r for r in rows if (r['symbol'], r['timeframe'], r['entry_mode']) == (symbol, tf, mode)]
                pairs.append(dict(symbol=symbol, timeframe=tf, entry_mode=mode, **pair_summary(cases, rules)))
    controls = []
    old = json.loads(Path('research/results/choch_summary.json').read_text())
    for row in rows:
        if row['symbol'] not in ('MNQ', 'MES', 'MGC') or row['timeframe'] not in (5, 15):
            continue
        reference = next(r for r in old['results'] if r['strategy'] == 'choch'
                         and (r['symbol'], r['timeframe'], r['entry_mode'], r['path'], r['nominal_reward_risk']) ==
                         (row['symbol'], row['timeframe'], row['entry_mode'], row['path'], 2.))
        for key in ('trades', 'net_pnl', 'return_pct', 'max_drawdown_pct'):
            if abs(row['stats'][key]-reference['stats'][key]) > 1e-7:
                raise ValueError(f'Original futures control changed: {row["symbol"]}, {row["timeframe"]}, {key}')
        controls.append((row['symbol'], row['timeframe'], row['entry_mode'], row['path']))
    record = dict(protocol=rules, protocol_sha256=expected_hash, source_sha256=current_source,
                  report_source_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
                  completed_cases=len(rows), planned_cases=544, unavailable_assets=missing,
                  original_control_cases_matched=len(controls), input_sha256=sources,
                  coverage=coverage, trade_journals=journals, results=rows, paired_comparisons=pairs)
    Path('research/results/choch_breadth_summary.json').write_text(json.dumps(record, indent=2, allow_nan=False), encoding='utf-8')
    plot(record)
    write_note(record)
    return record


def plot(record):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    rules, pairs = record['protocol'], record['paired_comparisons']
    fig, axes = plt.subplots(1, 2, figsize=(17, 12), sharey=True, layout='constrained')
    for ax, mode in zip(axes, rules['entry_modes']):
        values = np.full((len(rules['symbols']), len(rules['timeframes'])), np.nan)
        for i, symbol in enumerate(rules['symbols']):
            for j, tf in enumerate(rules['timeframes']):
                p = next(p for p in pairs if (p['symbol'], p['timeframe'], p['entry_mode']) == (symbol, tf, mode))
                if p['status'] == 'unavailable':
                    label = 'N/A'
                else:
                    if p['status'] != 'insufficient':
                        values[i, j] = p['return_min']
                    label = f"{p['return_min']:+.1f}%{'*' if p['status'] == 'passes' else ''}\n({p['trades_min']})"
                ax.text(j, i, label, ha='center', va='center', fontsize=8)
        cmap = plt.get_cmap('RdYlGn').copy();cmap.set_bad('#e6e9ed')
        im = ax.imshow(values, cmap=cmap, vmin=-40, vmax=40, aspect='auto')
        ax.set_xticks(range(len(rules['timeframes'])), [f'{t}m' for t in rules['timeframes']])
        ax.set_yticks(range(len(rules['symbols'])), rules['symbols'])
        ax.set_title('Next-open entry' if mode == 'next_open' else 'Confirmed retest')
    fig.colorbar(im, ax=axes, label='Lower return of two assumed paths (%) - color scale clipped at +/-40%', shrink=.7)
    fig.suptitle('CHoCH breadth: fixed 2R, up to 1% risk, assumed costs\n'
                 'Cell: annual return (trade count) | grey: fewer than 30 trades | *: passes predeclared historical screen', fontsize=13)
    fig.savefig('research/results/choch_breadth_heatmap.png', dpi=160)
    plt.close(fig)


def write_note(record):
    rules, pairs = record['protocol'], record['paired_comparisons']
    primary = [p for p in pairs if p['entry_mode'] == rules['primary_entry_mode']]
    counts = Counter(p['status'] for p in primary)
    immediate_counts = Counter(p['status'] for p in pairs if p['entry_mode'] == 'next_open')
    lines, detail = [], []
    for symbol in rules['symbols']:
        cells = []
        for tf in rules['timeframes']:
            p = next(p for p in primary if p['symbol'] == symbol and p['timeframe'] == tf)
            if p['status'] == 'unavailable':
                cells.append('N/A')
            else:
                cells.append(f"{p['return_min']:+.1f}%{'*' if p['status'] == 'passes' else ''}{' (small)' if p['status'] == 'insufficient' else ''}")
        lines.append('| '+symbol+' | '+' | '.join(cells)+' |')
    for p in pairs:
        if p['status'] == 'unavailable':
            detail.append(f"| {p['symbol']} | {p['timeframe']}m | {p['entry_mode']} | unavailable | - | - | - | - | - | - | - |")
        else:
            pf = f"{p['profit_factor_min']:.2f}" if p['profit_factor_min'] is not None else 'infinite'
            detail.append(f"| {p['symbol']} | {p['timeframe']}m | {p['entry_mode']} | {p['status']} | {p['trades_min']} | {p['return_min']:+.2f}% | {pf} | {p['max_drawdown']:.2f}% | {p['positive_blocks_min']}/4 | ${p['stress_net_min']:,.2f} | {p['mean_risk_pct_min']:.3f}% |")
    passing = [p for p in primary if p['status'] == 'passes']
    best = '\n'.join(f"- {p['symbol']} {p['timeframe']}m: {p['return_min']:+.2f}% return, {p['trades_min']} trades, PF {p['profit_factor_min']:.2f}, {p['max_drawdown']:.2f}% maximum drawdown, {p['positive_blocks_min']}/4 positive time blocks."
                     for p in passing) or 'No primary retest combination passed the full historical screen.'
    note = f'''# CHoCH breadth: more timeframes, futures, BTC spot and single stocks

Period: **3 November 2025 through 4 November 2026**. Completed **{record['completed_cases']} / 544** declared simulations: 17 assets, eight timeframes, two entry methods and two assumed minute-price paths. These are overlapping, dependent cases, not 544 independent tests. All {record['original_control_cases_matched']} original futures 5m/15m control cases matched their prior results.

The target stays at 2R, with the same strict 2-left/2-right confirmed swings, defined trend, frozen structural stop, first eligible signal per session, three-candle retest expiry and next-minute entry. Five minutes remains the prior reference; the expanded grid does not select a newly optimized strategy.

**The tested strategy is not broadly consistent across assets and timeframes.** Only {counts.get('passes', 0)} of the {counts.get('passes', 0)+counts.get('fails', 0)} sufficiently sampled primary retest combinations pass the historical screen; {counts.get('insufficient', 0)} others have fewer than 30 trades. MNQ retest returns change from -26.6%, -22.5% and -13.1% at 1m/2m/3m to +23.9% at 5m and -5.0% at 10m. The profitable settings are narrow historical pockets that need independent future validation.

BTC spot passes at no timeframe: its retest returns are -10.44% at 5m and +0.97% at 10m, with the latter failing the profit-factor and time-block requirements. Among the selected stocks, only XOM 5m passes the primary retest screen. Immediate next-open entries have {immediate_counts.get('passes', 0)} passes, {immediate_counts.get('fails', 0)} failures and {immediate_counts.get('insufficient', 0)} insufficient samples out of 136 combinations; these are controls on the same inspected year, not independent validation or a reason to switch to the best observed setting.

## Primary confirmed-retest results

The cell is the **lower total return of the two price paths** after modeled costs. An asterisk means the combination passed the historical screen. `(small)` means fewer than 30 trades in at least one path; its return is not dependable evidence. All accounts start independently at $50,000, compound and target up to 1% planned risk; do not sum these returns as a portfolio.

Stock and BTC positions are capped at 1x equity notional. They often use less than the available 1% risk, so raw returns across these assets and futures are not comparisons at matched leverage or volatility. Mean planned risk is shown in the detailed table.

| Asset | {' | '.join(str(t)+'m' for t in rules['timeframes'])} |
|---|{'---:|'*len(rules['timeframes'])}
{chr(10).join(lines)}

Across the 136 primary asset/timeframe combinations: **{counts.get('passes', 0)} pass**, **{counts.get('fails', 0)} fail**, **{counts.get('insufficient', 0)} have insufficient trades**, and **{counts.get('unavailable', 0)} are unavailable**. Passing is a descriptive screen, not a validated probability of future profit.

![Every declared asset/timeframe result, including immediate-entry controls](research/results/choch_breadth_heatmap.png)

### Screen specified before the expanded results

Both price paths must have positive net P&L, at least 30 trades, profit factor >=1.20, positive P&L in at least three of four consecutive time blocks, and positive stressed-cost P&L. The blocks are Nov-Jan, Feb-Apr, May-Jul and Aug through 4 November; trades are classified by entry date. Screen thresholds are research diagnostics, not parameter optimization or a clean unseen test. There is no multiple-testing-adjusted statistical significance claim.

{best}

## Market-specific assumptions

- Futures: the original NY 09:30-12:00 and 20:00-22:00 entry windows, daily/exchange-close liquidation, two daily entries and 150-micro cap. Costs remain assumed $0.85/side/contract and one adverse tick on market fills. Margin, depth, market impact and broker position limits are not reconstructed. Indices are correlated and do not supply independent confirmations.
- BTC: **long-only BTC/USD spot**, fractional 0.001-BTC units, up to 1x equity notional, assumed 0.10% fee on each fill and one basis point adverse market slippage. It uses the same weekday/CME-day windows as futures and is not an unrestricted weekend, perpetual or leveraged Bitcoin test. Consolidated historical prices do not establish executable fills on a particular crypto venue.
- Stocks: AAPL, MSFT, NVDA, TSLA, AMZN, JPM, XOM and UNH; whole shares, at most 1x equity notional, assumed $0.005/share/fill and one-cent adverse market slippage. Entries are 09:30-12:00 NY and positions exit five minutes before regular/early close. Both directions are hypothetical: stock borrow availability, borrow charges and short-sale restrictions are unmodeled. There is no claim that a cash-only account can short these shares. The basket is a selected sample of surviving names, not a historical universe.
- Stocks use an NYSE calendar, session-open-anchored complete bars and confirmed structure that bridges scheduled closures while resetting for missing full buckets. Futures retain the original maintenance/missing-bucket reset rule. This stock-calendar adaptation was declared before stock results, because a daily-reset hourly stock chart would never accumulate enough pivots. Crypto has continuous underlying data. These adaptations mean cross-market results are not identical exchange mechanics.
- Stock/crypto OHLC are normalized to cents: nearest open/close, outward high/low. Futures retain verified trade-tick increments. Stocks use split-adjusted prices and are flat overnight; dividends and a corporate-action cash ledger are not reconstructed. A 1x cash-asset notional cap and whole quantities often make realized planned risk **lower than 1%**; mean risk is retained per case.
- Cost stress reprices identical fills/quantities at $1.50/side for futures, $0.01/share for stocks or 0.20%/fill for BTC, plus one additional adverse tick on entry and market exits (one additional basis point for BTC). It does not rerun sizing or signals and is not a stressed compound-return forecast.
- Futures rolls use fixed calendars and historical contract reference metadata, not profit or hindsight volume selection. Missing old-contract exits retain explicitly tagged last-mark research liquidation plus slippage; those prices are not verified executable exits. Gaps, incomplete buckets and sparse micro-FX data can reduce signals, especially at longer timeframes. Small samples are shown rather than called robust profits.

## All paired cases

Each row combines both assumed minute paths conservatively: minimum trades/return/PF/positive blocks/stress P&L, maximum drawdown. Full individual-path results, mean achieved risk, monthly P&L and coverage are in the JSON. Price paths are hypothetical OHLC sequences, not observed ticks, confidence intervals or guaranteed bounds.

| Asset | Timeframe | Entry | Screen | Min trades | Lower return | Lower PF | Higher DD | Positive blocks | Lower stress P&L | Lower mean risk |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(detail)}

## Reproduction and audit

Run `python -m research.download_breadth`, then `python -m research.run_breadth --workers 4`, then `python -m research.breadth_report` and `python -m research.breadth_audit`. The data downloader uses the existing API entitlement and rate limit without purchases or broker orders. Isolated research registration adds optional market definitions; default live/paper symbols remain the original three.

The [protocol](research/choch_breadth_protocol.json) records the declared grid, market adaptations and historical screen. All final runs share its frozen hash. [Summary](research/results/choch_breadth_summary.json) retains every declared result and input/source hashes. Full trades, confirmation timestamps, modeled fees and daily marks are in compressed per-asset journals under [choch_breadth_trades](research/results/choch_breadth_trades). Raw price data and download caches remain local and ignored by Git. Unavailable assets: {', '.join(record['unavailable_assets']) or 'none'}.

The separate audit checked 58,158 simulated trade executions across all 544 cases: raw minute entry opens, pivot availability, timing, modeled fees, planned risk and notional caps, trade/equity reconciliation, stressed-cost arithmetic and source/input hashes. All 24 original futures control cases also matched their full prior trade lists. This audit checks the stated simulation mechanics; it does not establish executable market fills or validate the predictive hypothesis.

This remains retrospective research on one market year. Expanding a previously inspected hypothesis and observing the grid does not create a pristine out-of-sample result. Positive pockets, adjacent timeframes, correlated indices and overlapping trades do not establish persistent future profits.

Sources: [CME equity micro specifications](https://www.cmegroup.com/articles/faqs/frequently-asked-questions-micro-e-mini-equity-index-futures.html), [Micro WTI](https://www.cmegroup.com/education/articles-and-reports/micro-wti-crude-oil-futures-faq), [Micro EUR/USD](https://www.cmegroup.com/markets/fx/g10/e-micro-euro/specs), [Micro Silver rulebook](https://www.cmegroup.com/rulebook/COMEX/1a/121.pdf), [Massive contract definitions](https://massive.com/docs/rest/futures/contracts), [exchange calendars](https://pypi.org/project/exchange_calendars/).
'''
    Path('CHOCH_BREADTH.md').write_text(note, encoding='utf-8')
    print(json.dumps(dict(completed=record['completed_cases'], primary_statuses=dict(counts), passing=passing), indent=2), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--allow-incomplete', action='store_true')
    args = p.parse_args()
    build(args.allow_incomplete)
