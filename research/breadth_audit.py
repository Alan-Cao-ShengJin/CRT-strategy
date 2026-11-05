"""Independent trade-ledger and raw-minute audit of the expanded research output."""
from collections import Counter
from datetime import timedelta
from hashlib import sha256
from math import ceil
from pathlib import Path
import argparse
import csv
import gzip
import json

from crt.models import timestamp
from crt.strategy import session_at, trading_day
from research.breadth_study import PROTOCOL, ROOT, config, source_hashes
from research.expanded_markets import market, register


def audit(symbols=None):
    register()
    rules = json.loads(PROTOCOL.read_text())
    expected_source = source_hashes()
    originals = json.loads(Path('research/results/choch_summary.json').read_text())
    cases = trades_checked = controls = 0
    for symbol in (symbols or rules['symbols']):
        checkpoint = json.loads((ROOT/f'{symbol}.json').read_text())
        assert checkpoint['protocol_sha256'] == sha256(PROTOCOL.read_bytes()).hexdigest()
        assert checkpoint['source_sha256'] == expected_source
        assert len(checkpoint['results']) == 32
        for path, digest in checkpoint['input_sha256'].items():
            assert sha256(Path(path).read_bytes()).hexdigest() == digest
        path = Path(f'research/results/choch_breadth_trades/{symbol}.jsonl.gz')
        with gzip.open(path, 'rt', encoding='utf-8') as stream:
            full = [json.loads(line) for line in stream]
        assert len(full) == 32
        wanted = {(t['contract'], t['entry_time']) for row in full for t in row['trades']}
        opens = {}
        for record in checkpoint['acquisition']['records']:
            with Path(record['file']).open(newline='') as stream:
                for r in csv.DictReader(stream):
                    key = (r['contract'], r['timestamp'])
                    if key in wanted:
                        opens[key] = float(r['open'])
        m = market(symbol)
        for row in full:
            cases += 1
            cfg = config(symbol, row['path'], rules, checkpoint['acquisition']['schedule_file'])
            equity, prior_exit = 50000., None
            byday, sessions = Counter(), Counter()
            penalty = 0.
            for t in row['trades']:
                trades_checked += 1
                at, exited = timestamp(t['entry_time']), timestamp(t['exit_time'])
                assert exited >= at and (prior_exit is None or at >= prior_exit)
                assert rules['start_trading_date'] <= trading_day(at) < rules['end_exclusive_trading_date']
                session = session_at(at, cfg)
                assert session is not None
                byday[trading_day(at)] += 1;sessions[session] += 1
                meta = row['setups'][t['setup_id']]
                assert timestamp(meta['broken_pivot_time']) < timestamp(meta['pivot_known_at']) <= timestamp(meta['change_confirmed']) <= timestamp(meta['entry_confirmation']) == at
                assert t['stop'] == meta['structural_stop']
                delay = (at-timestamp(meta['change_confirmed'])).total_seconds()/60
                assert delay == 0 if row['entry_mode'] == 'next_open' else row['timeframe'] <= delay <= 3*row['timeframe']
                raw = opens[(t['contract'], t['entry_time'])]
                if m.kind == 'crypto':
                    assert t['side'] == 1
                    entry_slip = ceil(raw*rules['crypto_slippage_bps']/10000/m.tick-1e-8)*m.tick
                    stop_slip = ceil(t['stop']*rules['crypto_slippage_bps']/10000/m.tick-1e-8)*m.tick
                    stop_fill = t['stop']-t['side']*stop_slip
                    fee = (t['entry']+t['exit'])*t['qty']*m.point_value*rules['crypto_fee_rate']
                    planned_fee = (t['entry']+abs(stop_fill))*m.point_value*rules['crypto_fee_rate']
                    entry_fee = t['entry']*t['qty']*m.point_value*rules['crypto_fee_rate']
                    penalty += (t['entry']+t['exit'])*t['qty']*m.point_value*(rules['crypto_stress_fee_rate']-rules['crypto_fee_rate'])
                    penalty += (t['entry']+(t['exit'] if t['reason'] != 'target' else 0))*t['qty']*m.point_value*rules['crypto_slippage_bps']/10000
                else:
                    entry_slip = m.tick
                    stop_fill = t['stop']-t['side']*m.tick
                    rate = rules['stock_commission_per_share'] if m.kind == 'stock' else rules['futures_commission_per_side']
                    stress_rate = rules['stock_stress_commission_per_share'] if m.kind == 'stock' else rules['futures_stress_commission_per_side']
                    fee, planned_fee, entry_fee = 2*t['qty']*rate, 2*rate, t['qty']*rate
                    penalty += 2*t['qty']*(stress_rate-rate)+t['qty']*m.tick*m.point_value*(1+int(t['reason'] != 'target'))
                assert abs(t['entry']-(raw+t['side']*entry_slip)) < 1e-7
                assert abs(t['fees']-fee) < 1e-6
                assert abs(t['net']-((t['exit']-t['entry'])*t['side']*t['qty']*m.point_value-fee)) < 1e-5
                planned = (abs(t['entry']-stop_fill)*m.point_value+planned_fee)*t['qty']
                assert abs(t['risk']-planned) < 1e-5 and planned <= equity*.01+1e-5
                assert t['qty'] > 0 and int(t['qty']) == t['qty']
                if m.kind == 'futures':
                    assert t['qty'] <= 150
                else:
                    assert t['entry']*t['qty']*m.point_value+entry_fee <= equity+1e-5
                distance = t['side']*(t['entry']-t['stop'])
                reward = t['side']*(t['target']-t['entry'])
                assert reward+1e-7 >= 2*distance and reward-2*distance < m.tick+1e-7
                equity += t['net'];prior_exit = exited
            assert max(byday.values(), default=0) <= 2 and max(sessions.values(), default=0) <= 1
            assert abs(equity-row['stats']['ending_equity']) < 1e-5
            assert abs(equity-50000-row['stats']['net_pnl']) < 1e-5
            assert abs(row['stats']['stress_net_pnl']-(row['stats']['net_pnl']-penalty)) < 1e-5
            if symbol in ('MNQ', 'MES', 'MGC') and row['timeframe'] in (5, 15):
                reference = next(r for r in originals['results'] if r['strategy'] == 'choch'
                    and (r['symbol'], r['timeframe'], r['entry_mode'], r['path'], r['nominal_reward_risk']) ==
                        (symbol, row['timeframe'], row['entry_mode'], row['path'], 2.))
                assert row['trades'] == reference['trades']
                controls += 1
        print(json.dumps(dict(symbol=symbol, cases=32, ledger_and_input_audit='passed')), flush=True)
    result = dict(cases=cases, trade_executions=trades_checked, original_trade_for_trade_controls=controls,
                  status='passed', protocol_sha256=sha256(PROTOCOL.read_bytes()).hexdigest(),
                  source_sha256=expected_source)
    if symbols is None:
        summary = Path('research/results/choch_breadth_summary.json')
        result['summary_sha256'] = sha256(summary.read_bytes()).hexdigest()
        result['journal_sha256'] = {s: sha256(Path(f'research/results/choch_breadth_trades/{s}.jsonl.gz').read_bytes()).hexdigest()
                                    for s in rules['symbols']}
        result['audit_source_sha256'] = sha256(Path(__file__).read_bytes()).hexdigest()
        Path('research/results/choch_breadth_audit.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--symbols', nargs='+')
    args = p.parse_args()
    audit(args.symbols)
