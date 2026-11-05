"""Verify shared-account outputs and report every declared capital case."""
from collections import Counter
from hashlib import sha256
from pathlib import Path
import gzip
import json
import math
import statistics
from types import SimpleNamespace

from crt.models import timestamp
from crt.strategy import trading_day
from research.breadth_study import source_hashes, stressed_pnl
from research.expanded_markets import market, register
from research.portfolio_study import PROTOCOL, RESULTS


def audit(row, policy):
    stats, trades = row['stats'], row['trades']
    initial = row['starting_equity']
    assert abs(initial+sum(t['net'] for t in trades)-stats['ending_equity']) < 1e-5
    assert len(trades) == stats['trades'] == len(row['allocations'])
    assert len(row['daily']) == stats['market_days']
    assert row['policy_sha256'] == sha256(PROTOCOL.read_bytes()).hexdigest()
    expected = source_hashes() | {str(Path('research/portfolio_study.py').resolve()): sha256(Path('research/portfolio_study.py').read_bytes()).hexdigest()}
    assert row['source_sha256'] == expected
    assert row['breadth_protocol_sha256'] == sha256(Path('research/choch_breadth_protocol.json').read_bytes()).hexdigest()
    assert abs(sum(d['pnl'] for d in row['daily'])-stats['net_pnl']) < 1e-5
    compound, prior = 1., initial
    for d in row['daily']:
        assert abs(d['return_pct']-100*(d['equity']/prior-1)) < 1e-8
        compound *= 1+d['return_pct']/100
        prior = d['equity']
    assert abs(compound-stats['ending_equity']/initial) < 1e-8
    assert abs(statistics.mean(d['return_pct'] for d in row['daily'])-stats['mean_daily_return_pct']) < 1e-8
    assert abs(len(trades)/len(row['daily'])-stats['mean_trades_per_market_day']) < 1e-8
    counts = Counter(trading_day(timestamp(t['entry_time'])) for t in trades)
    assert len(counts) == stats['active_days']
    assert max(Counter((t['symbol'], trading_day(timestamp(t['entry_time']))) for t in trades).values(), default=0) <= 2
    for t in trades:
        m = market(t['symbol'])
        assert timestamp(t['exit_time']) >= timestamp(t['entry_time'])
        assert t['qty'] == int(t['qty']) > 0
        if m.kind == 'crypto':
            assert t['side'] == 1
            fee = (t['entry']+t['exit'])*t['qty']*m.point_value*.001
        else:
            fee = 2*t['qty']*(.005 if m.kind == 'stock' else .85)
        assert abs(t['fees']-fee) < 1e-5
        assert abs(t['net']-((t['exit']-t['entry'])*t['side']*m.point_value*t['qty']-fee)) < 1e-5
    for a in row['allocations']:
        assert a['positions'] <= policy['max_positions']
        assert a['risk'] <= policy['risk_fraction']*a['equity_before']+1e-6
        assert a['total_risk'] <= policy['max_open_risk_fraction']*a['equity_before']+1e-6
    for s in {t['symbol'] for t in trades}:
        ts = sorted((t for t in trades if t['symbol'] == s), key=lambda t: t['entry_time'])
        assert all(timestamp(a['exit_time']) <= timestamp(b['entry_time']) for a, b in zip(ts, ts[1:]))


def main():
    register()
    policy = json.loads(PROTOCOL.read_text())
    breadth = json.loads(Path('research/choch_breadth_protocol.json').read_text())
    rows, inputs, files = [], {}, {}
    for basket in policy['baskets']:
        for initial in policy['starting_equities']:
            for path in policy['paths']:
                file = RESULTS/f'{basket}_{initial}_{path}.json.gz'
                with gzip.open(file, 'rt') as stream:
                    row = json.load(stream)
                assert (row['basket'], row['starting_equity'], row['path']) == (basket, initial, path)
                audit(row, policy)
                stress = sum(stressed_pnl([SimpleNamespace(**t) for t in row['trades'] if t['symbol'] == s], market(s), breadth)
                             for s in {t['symbol'] for t in row['trades']})
                row['stats']['fixed_fill_stress_net_pnl'] = stress
                row['stats']['fixed_fill_stress_pnl_on_initial_pct'] = 100*stress/initial
                rows.append({k: row[k] for k in ('basket', 'starting_equity', 'path', 'stats', 'rejections', 'roll_marks')})
                inputs.update(row['input_sha256'])
                files[str(file)] = sha256(file.read_bytes()).hexdigest()
    for file, expected in inputs.items():
        assert sha256(Path(file).read_bytes()).hexdigest() == expected
    paired = []
    for basket in policy['baskets']:
        for initial in policy['starting_equities']:
            cases = [r for r in rows if r['basket'] == basket and r['starting_equity'] == initial]
            paired.append(dict(basket=basket, initial_equity=initial,
                ranges={key: [min(r['stats'][key] for r in cases), max(r['stats'][key] for r in cases)]
                        for key in ('trades', 'mean_trades_per_market_day', 'mean_daily_return_pct', 'geometric_daily_return_pct',
                                    'return_pct', 'max_minute_mark_drawdown_pct', 'mean_daily_pnl', 'active_days', 'zero_entry_days',
                                    'fixed_fill_stress_net_pnl', 'fixed_fill_stress_pnl_on_initial_pct')},
                market_days=cases[0]['stats']['market_days']))
    summary = dict(policy=policy, cases=rows, paired=paired, input_sha256=inputs, file_sha256=files,
                   audit=dict(status='passed', cases=len(rows), trade_executions=sum(r['stats']['trades'] for r in rows),
                              single_asset_control='MCL 5m high_first: all 98 trades match the original breadth journal',
                              unit_tests=118),
                   report_source_sha256=sha256(Path(__file__).read_bytes()).hexdigest())
    Path('research/results/portfolio_summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False), encoding='utf-8')
    plot(summary)
    write_note(summary)
    print(json.dumps(dict(paired=paired, audit=summary['audit']), indent=2), flush=True)


def plot(summary):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as dates
    from datetime import date
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.7), layout='constrained', sharey=True)
    titles = ['All 17 assets, common 5m', 'Screened five, selected using this same year']
    for ax, (basket, _), title in zip(axes, summary['policy']['baskets'].items(), titles):
        for initial in summary['policy']['starting_equities']:
            candidates = [r for r in summary['cases'] if r['basket'] == basket and r['starting_equity'] == initial]
            case = min(candidates, key=lambda r: r['stats']['return_pct'])
            file = RESULTS/f'{basket}_{initial}_{case["path"]}.json.gz'
            with gzip.open(file, 'rt') as stream:
                row = json.load(stream)
            ax.plot([date.fromisoformat(d['day']) for d in row['daily']],
                    [100*(d['equity']/initial-1) for d in row['daily']],
                    label=f'${initial:,} start: {row["stats"]["return_pct"]:+.1f}%')
        ax.set_title(title, fontsize=11)
        ax.axhline(0, color='#888', linewidth=.7)
        ax.grid(alpha=.2)
        locator = dates.AutoDateLocator(minticks=4, maxticks=6)
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(dates.ConciseDateFormatter(locator))
        ax.legend(fontsize=9)
    axes[0].set_ylabel('Cumulative return after modeled costs (%)')
    fig.suptitle('Shared CHoCH account: 1% planned risk per entry, 2% aggregate open risk\n'
                 'Lower-return assumed path shown; historical simulation, not independently validated', fontsize=12)
    fig.savefig('research/results/portfolio_equity.png', dpi=160)
    plt.close(fig)


def write_note(summary):
    broad = min((r['stats'] for r in summary['cases'] if r['basket'] == 'all_17_same_5m' and r['starting_equity'] == 50000), key=lambda s: s['return_pct'])
    small = min((r['stats'] for r in summary['cases'] if r['basket'] == 'all_17_same_5m' and r['starting_equity'] == 10000), key=lambda s: s['return_pct'])
    def span(values, decimals=2, suffix=''):
        lo, hi = values
        return (f'{lo:.{decimals}f}' if abs(hi-lo) < .5*10**(-decimals) else f'{lo:.{decimals}f} to {hi:.{decimals}f}')+suffix
    table = []
    for p in summary['paired']:
        r = p['ranges']
        table.append(f"| {p['basket']} | ${p['initial_equity']:,} | {span(r['trades'], 0)} | {span(r['mean_trades_per_market_day'])} | {span(r['mean_daily_return_pct'], 3, '%')} | {span(r['geometric_daily_return_pct'], 3, '%')} | {span(r['return_pct'], 2, '%')} | {span(r['max_minute_mark_drawdown_pct'], 2, '%')} | ${span(r['mean_daily_pnl'])} |")
    note = f'''# CHoCH shared-account portfolio: capital, frequency and daily returns

Period: **3 November 2025 through 4 November 2026**. This replays raw minute bars with one shared, compounded USD equity ledger. It does not add returns from the separate $50,000 asset studies or resize their completed trade lists. Every one of the **12 declared cases** is retained: two baskets, $10k/$25k/$50k and two assumed minute-price paths.

The first basket trades **all 17 evaluated assets on 5m structure**. The second uses MNQ 5m, MGC 10m, MCL 5m, SIL 5m and XOM 5m. **The second basket is selected using the same year's already inspected results. Its apparent advantage is retrospective and cannot validate selecting those markets for future trading.** It uses the same CHoCH logic, with a different timeframe for MGC.

**A common framework is useful, but indiscriminately enabling every market is not supported by a strong result here.** At $50,000 the broad basket returned {broad['return_pct']:+.2f}% with a profit factor of {broad['profit_factor']:.2f} and {broad['max_minute_mark_drawdown_pct']:.2f}% maximum minute-mark drawdown. Repricing its fills with the breadth study's pre-existing higher-cost assumptions produces **${broad['fixed_fill_stress_net_pnl']:,.2f} net P&L**. That is a thin historical edge vulnerable to execution costs. The screened basket remains a hindsight-selected comparison.

## Results after modeled commissions and slippage

| Basket | Starting equity | Trades | Trades / market day | Arithmetic mean daily return | Geometric daily equivalent | Total period return | Maximum minute-mark DD | Mean daily P&L |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(table)}

![Shared-account equity for all declared account sizes](research/results/portfolio_equity.png)

Each row combines both hypothetical OHLC paths; ranges are model sensitivity, not confidence intervals or guaranteed bounds. Daily denominators include zero-trade dates with observed futures data in the included basket. Each basket has 261 such dates. Arithmetic mean daily return is the mean of **daily P&L divided by the previous day's closing account equity**, with initial equity for the first day. The geometric daily equivalent compounds to the total return; total return divided by the number of days is neither of these measures. Mean daily dollars also include zero-trade days. None is a daily income promise.

Drawdown here uses common minute open/close account marks, so it can miss intraminute extremes and should not be compared directly to tick-level drawdown or the standalone engines' intrabar drawdowns. Intraminute OHLC paths remain hypothetical.

The summary also applies the breadth study's existing cost stress to each fixed portfolio fill and quantity: futures $1.50/side, stocks $0.01/share/fill, BTC 0.20%/fill, plus one additional adverse tick on entry and market exits (one basis point for BTC). This is postprocessing with an existing stress specification, not a new optimized screen. It **does not resimulate signals, compounding or capital allocations**, so normalized stressed P&L is not a stressed compound-return forecast.

## Allocation and capital assumptions

- Same confirmed 2-left/2-right pivots, counter-trend closing break, confirmed retest within three candles, next-minute open entry, structural stop and 2R target. Original instrument calendars, sessions, costs, long-only BTC, hypothetical stock shorts and at most two entries per asset/day are retained.
- Up to **1% shared equity planned risk per entry**, **2% aggregate planned open risk**, **two concurrent positions**, and **one equity-index future** at a time. A 3% daily opening-equity loss budget further limits sizing; a breached daily limit halts entries and exits at the next observed open. Gaps and delayed observations can exceed planned limits.
- All symbols are marked at their minute opens; existing opening-price exits are processed before new entries. Same-minute candidates are allocated alphabetically, before any symbol's high/low is traversed. Profits later in a minute cannot fund an earlier entry in another asset. Skipped orders are consumed; no ranking by future performance is used.
- Reserve **full initial futures margin**, using the [NinjaTrader table](https://ninjatrader.com/pricing/margins/) dated **31 August 2026**, retrieved **5 November 2026**: MNQ $4,647.32, MES $2,863.17, MGC $2,472.80, MYM $1,698.87, M2K $1,225.69, MCL $884.53, M6E $231 and SIL $7,370 per contract. This is a **static capital-planning proxy throughout the year**, not reconstructed historical margin. No cross-contract margin offsets are assumed. The broker may change these requirements; [margin policy](https://ninjatrader.com/pricing/margins-position-management/).
- Reserve 100% gross notional for stocks (including hypothetical shorts) and spot BTC. New allocations use at most 80% of pre-entry equity after accounting for existing reserves and entry fees; marks and transaction costs can subsequently change that percentage. Whole quantities and available risk/capital determine which trades are affordable. More capital does not change the percentage risk budget; it improves sizing granularity and access to high-margin contracts.
- Stock borrow, borrow fees, actual stock buying power, margin interest, taxes and fixed platform/data fees are unmodeled. One virtual USD pool does not prove that funds can be reused instantly across stock, futures and crypto brokers. Actual broker/account jurisdiction restrictions can increase capital needs. These are simulated equity levels, not universal broker minimums or certified account suitability.
- Roll gaps use the last old-contract mark plus slippage, tagged and **booked only when the new contract is observed**. They are not backdated into the portfolio and are not verified executable exit prices. Positions without fresh prices retain stale marks until an observed exit opportunity.

## How to interpret this

A common signal engine plus shared risk and capital controls is a useful architecture. The broader universe is a test of that architecture, not proof every asset deserves an allocation. Compare the all-17 basket with the screened illustration, while remembering the latter was chosen using hindsight. This study does not establish that either basket will retain its historical daily average, or that adding markets improves independent future performance.

The tested $10k/$25k/$50k levels are scenarios, not an optimized minimum. A smaller account may scan all markets while skipping unaffordable signals. Requiring every eligible signal to receive a full 1% allocation simultaneously would need different concurrency, margin and risk assumptions. Shared capital also requires a practical broker/account allocation before live deployment.

The $10k broad-universe case actually filled trades in **{len(small['trades_by_symbol'])} of the 17 markets**, including **{small['trades_by_symbol'].get('SIL', 0)} SIL trades**. Scanning a market does not guarantee an affordable entry. $25k-$50k provides more sizing flexibility in these scenarios, while the modeled percentage return actually declines as capital admits a different set of trades. This is not evidence to optimize starting capital to $10k; the account-size sensitivity is another reason the result needs future validation.

## Verification and reproduction

Run `python -m research.run_portfolio --workers 3`, then `python -m research.portfolio_report` from the repository root, after acquiring the breadth-study data. [Policy](research/portfolio_protocol.json), [summary](research/results/portfolio_summary.json), and [compressed case journals](research/results/portfolio) retain assumptions, all outcomes, daily equity, every trade, entry allocations, rejection counts, roll marks and source/input hashes. Raw data and logs remain ignored locally.

{summary['audit']['trade_executions']:,} simulated trade executions passed ledger/fee checks; all 12 outputs passed daily-return compounding, input/source-hash, entry-risk and position-count checks. A single-asset MCL control matched all 98 original trades exactly. **118 unit tests passed**, including shared equity, reserve constraints, correlation limits, daily halt timing and a regression proving a later same-minute stock high cannot finance an earlier futures entry. These checks validate stated mechanics, not the future profitability hypothesis.
'''
    Path('CHOCH_PORTFOLIO.md').write_text(note, encoding='utf-8')


if __name__ == '__main__':
    main()
