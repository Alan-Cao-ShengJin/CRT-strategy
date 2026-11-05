"""Run every fixed shared-account case with isolated bounded workers."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256
from pathlib import Path
import argparse
import gzip
import json
import subprocess
import sys

from research.breadth_study import source_hashes
from research.portfolio_study import PROTOCOL, RESULTS


def main(workers):
    rules = json.loads(PROTOCOL.read_text())
    policy_hash = sha256(PROTOCOL.read_bytes()).hexdigest()
    model = source_hashes() | {str(Path('research/portfolio_study.py').resolve()):
                               sha256(Path('research/portfolio_study.py').read_bytes()).hexdigest()}
    logs = Path('runs/portfolio');logs.mkdir(parents=True, exist_ok=True)
    def job(basket, equity, path):
        file = RESULTS/f'{basket}_{equity}_{path}.json.gz'
        if file.exists():
            with gzip.open(file, 'rt') as stream:
                old = json.load(stream)
            if old['policy_sha256'] != policy_hash or old['source_sha256'] != model:
                raise ValueError('Incompatible checkpoint: '+str(file))
            return dict(basket=basket, equity=equity, path=path, cached=True)
        with (logs/f'{basket}_{equity}_{path}.log').open('w') as stream:
            subprocess.run([sys.executable, '-u', '-m', 'research.portfolio_study', '--basket', basket,
                            '--equity', str(equity), '--path', path], stdout=stream, stderr=subprocess.STDOUT,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=True)
        return dict(basket=basket, equity=equity, path=path, cached=False)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs = [pool.submit(job, b, e, p) for b in rules['baskets'] for e in rules['starting_equities'] for p in rules['paths']]
        for i, future in enumerate(as_completed(jobs), 1):
            print(json.dumps(dict(completed=i, total=len(jobs), **future.result())), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=3)
    args = parser.parse_args()
    main(args.workers)
