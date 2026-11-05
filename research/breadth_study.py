"""Fixed-rule CHoCH breadth replay. Data acquisition and reporting are separate."""
from collections import defaultdict
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
import argparse
import gzip
import json
import sys

from crt.config import Config
from crt.data import read_bars
from crt.models import INSTRUMENTS, timestamp
from crt.strategy import trading_day
from research.choch_study import replay, trade_stats
from research.expanded_markets import ExpandedEngine, market, register

PROTOCOL = Path('research/choch_breadth_protocol.json')
ROOT = Path('runs/choch_breadth')


def source_hashes():
    paths = sorted(Path('crt').glob('*.py'))+[Path('research')/name for name in
            ('choch.py', 'choch_study.py', 'risk_sizing_study.py', 'expanded_markets.py', 'breadth_study.py')]
    return {str(p): sha256(p.read_bytes()).hexdigest() for p in paths}


def config(symbol, path, rules, schedule):
    m = market(symbol)
    cfg = Config(symbols=[symbol], entry_mode='rebreak', bar_model=path,
                 reward_risk=rules['reward_risk'], bias_enabled=False, chop_enabled=False, neutral_policy='first',
                 initial_equity=rules['initial_equity'], risk_fraction=rules['risk_fraction'],
                 risk_per_trade=1e12, max_open_risk=1e12, max_daily_loss=1e12, max_drawdown=1e12,
                 max_contracts=rules['futures_max_contracts'] if m.kind == 'futures' else 1000000000,
                 max_positions=1, min_target_ticks=1, min_stop_ticks=1, schedule_file=schedule)
    cfg.commission_per_side = {s: (rules['stock_commission_per_share'] if market(s).kind == 'stock' else
                                  0. if market(s).kind == 'crypto' else rules['futures_commission_per_side']) for s in INSTRUMENTS}
    if m.kind == 'stock':
        cfg.sessions = {'new_york': ['09:30', '12:00']}
        cfg.flatten_time = '15:55'
    return cfg.validate()


def stressed_pnl(trades, m, rules):
    penalty = 0.
    for t in trades:
        if m.kind == 'crypto':
            penalty += (t.entry+t.exit)*t.qty*m.point_value*(rules['crypto_stress_fee_rate']-rules['crypto_fee_rate'])
            penalty += t.entry*t.qty*m.point_value*rules['crypto_slippage_bps']/10000
            if t.reason != 'target':
                penalty += t.exit*t.qty*m.point_value*rules['crypto_slippage_bps']/10000
        else:
            difference = (rules['stock_stress_commission_per_share']-rules['stock_commission_per_share'] if m.kind == 'stock'
                          else rules['futures_stress_commission_per_side']-rules['futures_commission_per_side'])
            penalty += 2*t.qty*difference+t.qty*m.tick*m.point_value*(1+int(t.reason != 'target'))
    return sum(t.net for t in trades)-penalty


def collect(engine, rules, symbol, tf, mode, path):
    trades, m = engine.trades, market(symbol)
    stats = trade_stats(trades)
    stats.update(return_pct=100*(engine.equity/engine.config.initial_equity-1),
                 ending_equity=engine.equity, max_drawdown_pct=100*engine.max_fractional_drawdown,
                 fees=sum(t.fees for t in trades), stress_net_pnl=stressed_pnl(trades, m, rules),
                 complete_structure_candles=engine.complete_candles,
                 max_qty=max(engine.entry_quantities, default=0),
                 mean_planned_risk_pct=100*sum(engine.entry_risk_fractions)/len(engine.entry_risk_fractions) if engine.entry_risk_fractions else 0.,
                 max_planned_risk_pct=100*max(engine.entry_risk_fractions, default=0),
                 without_best_five=sum(t.net for t in trades)-sum(sorted((t.net for t in trades), reverse=True)[:5]))
    blocks = []
    for start, end in rules['blocks']:
        blocks.append(dict(start=start, end_exclusive=end, **trade_stats([
            t for t in trades if start <= trading_day(timestamp(t.entry_time)) < end])))
    stats['positive_blocks'] = sum(b['net_pnl'] > 0 for b in blocks)
    stats['blocks'] = blocks
    monthly = defaultdict(float)
    for t in trades:
        monthly[trading_day(timestamp(t.exit_time))[:7]] += t.net
    stats['monthly_realized_net_pnl'] = dict(sorted(monthly.items()))
    daily = {}
    for row in engine.curve:
        d = trading_day(timestamp(row['time']))
        if rules['start_trading_date'] <= d < rules['end_exclusive_trading_date']:
            daily[d] = row
    row = dict(symbol=symbol, asset_class=m.kind, timeframe=tf, entry_mode=mode, path=path,
               stats=stats, roll_data_liquidations=engine.roll_data_liquidations,
               exit_reasons={r: sum(t.reason == r for t in trades) for r in sorted({t.reason for t in trades})})
    full = dict(row, config=engine.config.to_dict(), market=asdict(m), daily_last_marks=daily,
                trades=[asdict(t) for t in trades],
                setups={t.setup_id: engine.setup_details[t.setup_id] for t in trades})
    assert abs(engine.equity-engine.config.initial_equity-sum(t.net for t in trades)) < 1e-5
    return row, full


def study(symbol):
    register()
    rules = json.loads(PROTOCOL.read_text())
    ready_path = Path(f'data/choch_expanded/READY_{symbol}.json')
    ready = json.loads(ready_path.read_text())
    inputs = ready['input_sha256'] | {ready['schedule_file']: ready['calendar_sha256']}
    for path, digest in inputs.items():
        if sha256(Path(path).read_bytes()).hexdigest() != digest:
            raise ValueError(f'Data changed since acquisition: {path}')
    record = dict(protocol_sha256=sha256(PROTOCOL.read_bytes()).hexdigest(), source_sha256=source_hashes(),
                  symbol=symbol, market=asdict(market(symbol)), acquisition=ready, input_sha256=inputs, results=[])
    destination = ROOT/f'{symbol}.json'
    directory = ROOT/symbol
    directory.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        previous = json.loads(destination.read_text())
        for key in ('protocol_sha256', 'source_sha256', 'input_sha256'):
            if previous[key] != record[key]:
                raise ValueError(f'Checkpoint {key} differs; use a fresh output directory')
        record = previous
    done = {(r['timeframe'], r['entry_mode'], r['path']) for r in record['results']}
    bars = read_bars([r['file'] for r in ready['records']])
    record['coverage'] = dict(minute_bars=len(bars), first=bars[0].start.isoformat(), last=bars[-1].end.isoformat(),
                              observed_trading_dates=len({trading_day(b.start) for b in bars
                               if rules['start_trading_date'] <= trading_day(b.start) < rules['end_exclusive_trading_date']}))
    for tf in rules['timeframes']:
        for mode in rules['entry_modes']:
            for path in rules['paths']:
                if (tf, mode, path) in done:
                    continue
                cfg = config(symbol, path, rules, ready['schedule_file'])
                engine = ExpandedEngine(cfg, tf, mode, rules['reward_risk'], rules)
                replay(engine, bars, rules['start_trading_date'], rules['end_exclusive_trading_date'])
                row, full = collect(engine, rules, symbol, tf, mode, path)
                (directory/f'{tf}_{mode}_{path}.json').write_text(json.dumps(full, allow_nan=False), encoding='utf-8')
                record['results'].append(row)
                destination.write_text(json.dumps(record, indent=2, allow_nan=False), encoding='utf-8')
                print(json.dumps({k: row[k] for k in ('symbol', 'timeframe', 'entry_mode', 'path')} |
                                 {k: row['stats'][k] for k in ('trades', 'return_pct', 'profit_factor', 'max_drawdown_pct', 'positive_blocks')}), flush=True)
    export_journal(record, directory)
    return record


def export_journal(record, directory):
    path = Path('research/results/choch_breadth_trades')/f'{record["symbol"]}.jsonl.gz'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('wb') as raw:
        with gzip.GzipFile(fileobj=raw, mode='wb', mtime=0) as stream:
            for r in record['results']:
                file = directory/f'{r["timeframe"]}_{r["entry_mode"]}_{r["path"]}.json'
                stream.write(file.read_bytes()+b'\n')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--symbol', required=True)
    args = p.parse_args()
    study(args.symbol)
