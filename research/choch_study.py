"""Frozen-rule CHoCH versus matched CRT replay on cached dated futures."""
from collections import defaultdict
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
import argparse
import json

from crt.config import Config
from crt.data import read_bars
from crt.models import INSTRUMENTS, timestamp
from crt.strategy import trading_day
from research.choch import ChochEngine
from research.risk_sizing_study import EquityRiskEngine

PROTOCOL = Path('research/choch_protocol.json')


def config(symbol, path, rules):
    cfg = Config(symbols=[symbol], bar_model=path, entry_mode='rebreak',
                 bias_enabled=False, chop_enabled=False, neutral_policy='first',
                 initial_equity=rules['initial_equity'], risk_fraction=rules['risk_fraction'],
                 risk_per_trade=1e12, max_open_risk=1e12, max_daily_loss=1e12, max_drawdown=1e12,
                 max_contracts=rules['max_contracts'], max_positions=1,
                 min_target_ticks=1, min_stop_ticks=1, slippage_ticks=rules['slippage_ticks'],
                 schedule_file='data/annual/schedules.json')
    cfg.commission_per_side = {s: rules['commission_per_side'] for s in INSTRUMENTS}
    return cfg.validate()


def replay(engine, bars, start, end):
    for bar in bars:
        day = trading_day(bar.start)
        if day >= end:
            break
        state = engine.states[bar.symbol]
        if state.contract is not None and state.contract != bar.contract and bar.symbol in engine.positions:
            last = state.last_bar
            engine._exit(bar.symbol, engine.marks[bar.symbol], last.end, 'roll_data_end_mark', market=True)
            trade = engine.trades[-1]
            engine.roll_data_liquidations.append(dict(contract=trade.contract, time=trade.exit_time,
                                                      net=trade.net, next_contract_first_bar=bar.start.isoformat()))
            engine._risk_check(last.end)
        engine.on_bar_batch([bar], learn_only=day < start)
    engine.finish()
    if engine.last_time:
        engine._risk_check(engine.last_time)
    return engine


def trade_stats(trades):
    wins = sum(t.net > 0 for t in trades)
    gain = sum(max(0, t.net) for t in trades)
    loss = -sum(min(0, t.net) for t in trades)
    return dict(trades=len(trades), net_pnl=sum(t.net for t in trades),
                win_rate_pct=100*wins/len(trades) if trades else None,
                profit_factor=gain/loss if loss else None,
                mean_net_r=sum(t.r_multiple for t in trades)/len(trades) if trades else None)


def result(engine, rules, strategy, symbol, path, timeframe=None, mode=None, rr=None):
    stats = trade_stats(engine.trades)
    stats.update(return_pct=100*(engine.equity/engine.config.initial_equity-1),
                 ending_equity=engine.equity, max_drawdown_pct=100*engine.max_fractional_drawdown,
                 fees=sum(t.fees for t in engine.trades),
                 commission_stress_net_pnl=stats['net_pnl']-2*sum(t.qty for t in engine.trades)*(
                     rules['cost_stress_commission_per_side']-rules['commission_per_side']),
                 max_contracts_used=max(engine.entry_quantities, default=0),
                 max_planned_risk_pct=100*max(engine.entry_risk_fractions, default=0),
                 contract_cap_hits=sum(q == rules['max_contracts'] for q in engine.entry_quantities))
    equity = engine.config.initial_equity
    leverages = []
    for t in engine.trades:
        leverages.append(t.entry*t.qty*INSTRUMENTS[t.symbol].point_value/equity)
        equity += t.net
    stats['max_entry_notional_to_equity'] = max(leverages, default=0)
    stats['early_entry_cohort'] = trade_stats([t for t in engine.trades if trading_day(timestamp(t.entry_time)) < rules['chronological_split']])
    stats['late_entry_cohort'] = trade_stats([t for t in engine.trades if trading_day(timestamp(t.entry_time)) >= rules['chronological_split']])
    monthly = defaultdict(float)
    for t in engine.trades:
        monthly[trading_day(timestamp(t.exit_time))[:7]] += t.net
    stats['monthly_realized_net_pnl'] = dict(sorted(monthly.items()))
    daily = {}
    for point in engine.curve:
        daily[trading_day(timestamp(point['time']))] = point
    reasons = {r: sum(t.reason == r for t in engine.trades) for r in sorted({t.reason for t in engine.trades})}
    return dict(strategy=strategy, symbol=symbol, path=path, timeframe=timeframe, entry_mode=mode,
                nominal_reward_risk=rr, config=engine.config.to_dict(), stats=stats,
                roll_data_liquidations=engine.roll_data_liquidations, exit_reasons=reasons,
                daily_last_marks=daily, trades=[asdict(t) for t in engine.trades],
                setups={t.setup_id: engine.setup_details[t.setup_id] for t in engine.trades} if strategy == 'choch' else {})


def source_hashes():
    return {str(p): sha256(p.read_bytes()).hexdigest() for p in
            sorted(Path('crt').glob('*.py'))+sorted(Path('research').glob('choch*.py'))}


def study(symbols):
    rules = json.loads(PROTOCOL.read_text())
    manifest = json.loads(Path('data/annual/manifest.json').read_text())
    records, destinations = [], []
    for symbol in symbols:
        record = dict(protocol=rules, protocol_sha256=sha256(PROTOCOL.read_bytes()).hexdigest(),
                      source_sha256=source_hashes(), input_sha256={}, results=[])
        inputs = [r['file'] for r in manifest if r['root'] == symbol]
        for file in inputs+['data/annual/manifest.json', 'data/annual/schedules.json']:
            record['input_sha256'][file] = sha256(Path(file).read_bytes()).hexdigest()
        bars = read_bars(inputs)
        destination = Path(f'runs/choch/{symbol}.json')
        destination.parent.mkdir(parents=True, exist_ok=True)
        destinations.append(str(destination))
        cases = [(None, None, None, path) for path in rules['paths']]
        cases += [(tf, mode, rr, path) for tf in rules['timeframes'] for mode in rules['entry_modes']
                  for rr in rules['reward_risks'] for path in rules['paths']]
        for tf, mode, rr, path in cases:
            cfg = config(symbol, path, rules)
            engine = (EquityRiskEngine(cfg) if tf is None else
                      ChochEngine(cfg, tf, mode, rr, rules['retest_expiry_bars'], rules['pivot_left']))
            replay(engine, bars, rules['start_trading_date'], rules['end_exclusive_trading_date'])
            row = result(engine, rules, 'crt_rebreak' if tf is None else 'choch', symbol, path, tf, mode, rr)
            record['results'].append(row)
            destination.write_text(json.dumps(record, indent=2, allow_nan=False), encoding='utf-8')
            print(json.dumps({k: row[k] for k in ('strategy', 'symbol', 'timeframe', 'entry_mode', 'nominal_reward_risk', 'path')} |
                             {k: row['stats'][k] for k in ('trades', 'win_rate_pct', 'return_pct', 'profit_factor', 'max_drawdown_pct')}), flush=True)
        records.append(record)
    if set(symbols) == set(rules['symbols']) and len(symbols) == len(rules['symbols']):
        merge(destinations)
    return records


def merge(paths):
    docs = [json.loads(Path(p).read_text()) for p in paths]
    if len({d['protocol_sha256'] for d in docs}) != 1 or any(d['source_sha256'] != docs[0]['source_sha256'] for d in docs):
        raise ValueError('Study versions differ')
    if docs[0]['protocol_sha256'] != sha256(PROTOCOL.read_bytes()).hexdigest() or docs[0]['source_sha256'] != source_hashes():
        raise ValueError('Current source/protocol differ from runs')
    record = dict(docs[0])
    rows = [r for d in docs for r in d['results']]
    keys = [(r['symbol'], r['strategy'], r['timeframe'], r['entry_mode'], r['nominal_reward_risk'], r['path']) for r in rows]
    if len(rows) != 54 or len(set(keys)) != 54:
        raise ValueError('Expected all 54 distinct comparator and CHoCH cases')
    inputs = {}
    for d in docs:
        for p, digest in d['input_sha256'].items():
            if p in inputs and inputs[p] != digest:
                raise ValueError('Input mismatch')
            inputs[p] = digest
    record.update(results=rows, input_sha256=inputs)
    report(record)


def report(record):
    output = Path('research/results/choch_summary.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2, allow_nan=False), encoding='utf-8')
    rules, rows = record['protocol'], record['results']
    lines = []
    for symbol in rules['symbols']:
        combos = [('crt_rebreak', None, None, None)]+[('choch', tf, m, rr) for tf in rules['timeframes']
                  for m in rules['entry_modes'] for rr in rules['reward_risks']]
        for strategy, tf, mode, rr in combos:
            cases = [r for r in rows if (r['symbol'], r['strategy'], r['timeframe'], r['entry_mode'], r['nominal_reward_risk']) ==
                     (symbol, strategy, tf, mode, rr)]
            def span(key, fmt='.2f'):
                values = [c['stats'][key] for c in cases if c['stats'][key] is not None]
                return 'N/A' if not values else (f'{min(values):{fmt}}' if min(values) == max(values)
                                                else f'{min(values):{fmt}} to {max(values):{fmt}}')
            label = 'CRT rebreak' if strategy == 'crt_rebreak' else f'{tf}m {mode}, {rr:g}R'
            lines.append(f'| {symbol} | {label} | {span("trades", "d")} | {span("win_rate_pct")}% | {span("return_pct")}% | {span("profit_factor")} | {span("max_drawdown_pct")}% |')
    primary = [r for r in rows if r['strategy'] == 'choch' and r['timeframe'] == rules['primary_timeframe']
               and r['nominal_reward_risk'] == rules['primary_reward_risk']]
    primary_groups = []
    for symbol in rules['symbols']:
        for mode in rules['entry_modes']:
            cases = [r for r in primary if r['symbol'] == symbol and r['entry_mode'] == mode]
            primary_groups.append(dict(symbol=symbol, mode=mode,
                return_pct=[r['stats']['return_pct'] for r in cases],
                positive_both_paths=all(r['stats']['net_pnl'] > 0 for r in cases),
                negative_both_paths=all(r['stats']['net_pnl'] < 0 for r in cases)))
    record['primary_comparison'] = primary_groups
    output.write_text(json.dumps(record, indent=2, allow_nan=False), encoding='utf-8')
    positive = sum(all(r['stats']['net_pnl'] > 0 for r in primary if r['symbol'] == s and r['entry_mode'] == m)
                   for s in rules['symbols'] for m in rules['entry_modes'])
    text = f'''# CHoCH futures test: fixed rules, realistic signal timing

Evaluation: **3 November 2025 through 4 November 2026**, on the existing dated MNQ, MES and MGC minute cache. The primary specification is **5-minute structure, 2R target**, with immediate and confirmed-retest entries. Fifteen-minute structure and 1.5R are separately declared sensitivities; they do not replace the primary result after inspection. All 48 CHoCH cases and six matched CRT comparators are reported.

## What was tested

A change of character means a counter-trend break of swing structure. Definitions vary, so this tests our explicit mechanical interpretation, rather than a particular trader's discretionary method. The conceptual reference describes the counter-trend break, closing confirmation and possible retracement entry; our pivot width, trend test, sessions, stop, expiry and targets are research choices. [Concept source](https://www.luxalgo.com/library/concept/change-of-character/).

- A swing high/low must exceed/be below the two candles on either side, strictly. It becomes usable only after the second right-hand candle closes. Outside candles qualifying as both are ignored. The latest two confirmed highs and lows must both rise for an uptrend, or both fall for a downtrend.
- A bearish signal closes at least one tick below the latest swing low in an uptrend; bullish is the mirror. Require a crossing from the other side and consume that pivot once. Mixed structure has no trade. Structure resets at missing timeframe buckets and contract changes.
- Stop: one tick beyond the most adverse observed extreme since the latest opposite swing, frozen at break confirmation. Target: 2R primary (1.5R sensitivity), calculated from actual slipped entry to the frozen stop and rounded outward to a tick.
- Immediate: enter at the immediately following minute open. Retest: within three following complete structure candles, touch the broken level and close a tick back on the breakout side; then enter next minute open. A stop touch while waiting invalidates the setup; a data gap cancels the waiting setup, and a missing next entry minute cancels the order.
- First eligible signal reserves each session, including an unfilled/expired retest. Use the existing 09:30-12:00 and 20:00-22:00 New York entry sessions, two daily entries, one open position, and existing daily/exchange flattening. No discretion, FVG, order-block or economic-news overlay is included.

Waiting for pivot confirmation is essential: platforms can display a pivot at its earlier location after later candles establish it. Entries here cannot occur before that information exists. [Pivot timing documentation](https://www.tradingview.com/pine-script-docs/faq/techniques/).

## Results

**{positive} of 6 primary instrument/entry combinations were profitable under both price paths.** This count is descriptive, not an independent probability estimate. The table includes every sensitivity to avoid selecting only a profitable variant.

Independent $50,000 accounts, **up to 1% current-equity planned risk**, compounding, whole micro contracts and a 150-contract research cap. Planned risk includes modeled round-trip fees and adverse stop slippage. Costs are the original CRT assumptions: $0.85 per side/contract and one adverse tick at entry and stop/market exits. The fresh CRT rebreak comparator uses the same account limits, costs, calendar and paths; its original combined 15/30/60-minute signal rules remain.

| Instrument | Specification | Trades | Net win rate | Total return | Profit factor | Maximum marked drawdown |
|---|---|---:|---:|---:|---:|---:|
{chr(10).join(lines)}

Ranges compare open-high-low-close and open-low-high-close minute paths. They are not actual ticks, confidence intervals or guaranteed profit bounds. Win rate counts net-positive trades after costs. Drawdown is peak-to-current equity percentage across processed path events, including unrealized P&L.

## Limits and audit trail

This is one previously inspected year. No profit-based parameter tuning occurred, but the cases share data, and the earlier/later entry-cohort split is retrospective. A favorable sensitivity is not an independently validated replacement strategy. Exact fills, queue position, bid/ask spreads and additional intraminute oscillations remain unknown. No claim about the profitability of every strategy called CHoCH follows from this test.

No margin, liquidity or market-impact model is imposed. Contract caps can reduce achieved risk; dollar and percentage returns are conditional sizing simulations, not demonstrated executable returns or a prop-firm qualification test. Multiple instrument returns are alternatives and must not be added into a shared account.

Some old-contract intervals are missing. As in the CRT sizing comparison, an open position at a roll uses its last observed mark plus adverse slippage, explicitly tagged `roll_data_end_mark`. Terminal open positions are `end_of_data` research liquidations. These are not verified fills; their timestamps and P&L contributions remain in the JSON. No missing prices are fabricated. Earlier/later cohorts classify trades by entry date; monthly P&L classifies by exit date.

The [protocol](research/choch_protocol.json) was written before these runs. [Full results](research/results/choch_summary.json) contain all configurations, source and input hashes, trades, pivot availability/confirmation times, daily last marks, cost-stress repricing, contract quantities, leverage estimates and gap-mark contributions. Cost stress increases commission to $1.50/side on the same fills; it does not resimulate sizing. Daily last marks are a storage sample; maximum drawdown uses all simulated risk events.

Reproduce each instrument with `python -m research.choch_study --symbols MNQ` (or MES/MGC), then `python -m research.choch_study --merge runs/choch/MNQ.json runs/choch/MES.json runs/choch/MGC.json`. The CHoCH engine is research-only; paper/live defaults remain unchanged.
'''
    Path('CHOCH_RESEARCH.md').write_text(text, encoding='utf-8')
    print(json.dumps(primary_groups, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--symbols', nargs='+', choices=['MNQ', 'MES', 'MGC'])
    parser.add_argument('--merge', nargs='+')
    args = parser.parse_args()
    if args.merge:
        merge(args.merge)
    else:
        study(args.symbols or ['MNQ', 'MES', 'MGC'])
