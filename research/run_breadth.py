"""Queue isolated research workers as immutable asset datasets become ready."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time


def checkpoint(symbol):
    path = Path(f'runs/choch_breadth/{symbol}.json')
    try:
        return len(json.loads(path.read_text())['results'])
    except (FileNotFoundError, json.JSONDecodeError):
        return 0


def main(workers, external):
    symbols = json.loads(Path('research/choch_breadth_protocol.json').read_text())['symbols']
    active, complete, failed = {}, set(), set()
    logs = Path('runs/choch_breadth')
    logs.mkdir(parents=True, exist_ok=True)
    last_progress = None
    while len(complete)+len(failed) < len(symbols):
        for symbol, (process, stream) in list(active.items()):
            code = process.poll()
            if code is None:
                continue
            stream.close()
            active.pop(symbol)
            (complete if code == 0 and checkpoint(symbol) == 32 else failed).add(symbol)
            print(json.dumps(dict(stage='worker_complete', symbol=symbol, exit_code=code,
                                  cases=checkpoint(symbol))), flush=True)
        external_active = 0
        for symbol in external:
            journal = Path(f'research/results/choch_breadth_trades/{symbol}.jsonl.gz')
            if checkpoint(symbol) == 32 and journal.exists():
                complete.add(symbol)
            elif symbol not in complete:
                external_active += 1
        for symbol in symbols:
            if symbol in complete or symbol in failed or symbol in active or symbol in external:
                continue
            if len(active)+external_active >= workers:
                break
            if not Path(f'data/choch_expanded/READY_{symbol}.json').exists():
                continue
            stream = (logs/f'{symbol}.log').open('a', encoding='utf-8')
            process = subprocess.Popen([sys.executable, '-u', '-m', 'research.breadth_study', '--symbol', symbol],
                                       stdout=stream, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            active[symbol] = process, stream
            print(json.dumps(dict(stage='worker_started', symbol=symbol, resumed_cases=checkpoint(symbol))), flush=True)
        progress = (sum(checkpoint(s) for s in symbols), tuple(sorted(complete)), tuple(sorted(active)), tuple(sorted(failed)))
        if progress != last_progress:
            print(json.dumps(dict(stage='progress', cases=progress[0], planned=544,
                                  complete=sorted(complete), active=sorted(active), failed=sorted(failed))), flush=True)
            last_progress = progress
        time.sleep(5)
    if failed:
        raise SystemExit('Research workers failed: '+', '.join(sorted(failed)))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workers', type=int, default=4)
    p.add_argument('--external', nargs='*', default=[])
    args = p.parse_args()
    main(args.workers, args.external)
