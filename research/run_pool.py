"""Queue frozen candidate replays as downloads finish, then evaluate the chosen pool."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256
from pathlib import Path
import argparse
import gzip
import json
import subprocess
import sys
import time

from research.pool_markets import DEST, PROTOCOL, ready_path, register_pool, universe
from research.pool_select import SCREEN, fingerprints, select_pool


def main(workers):
    register_pool()
    policy = json.loads(PROTOCOL.read_text())
    logs = Path('runs/pool');logs.mkdir(parents=True, exist_ok=True)
    complete, active, unavailable = set(), {}, {}
    symbols = universe()
    while len(complete)+len(unavailable) < len(symbols):
        status = DEST/'acquisition_status.json'
        if status.exists():
            unavailable = json.loads(status.read_text()).get('unavailable', {})
        for s, (process, stream) in list(active.items()):
            code = process.poll()
            if code is None:
                continue
            stream.close();active.pop(s)
            if code:
                raise RuntimeError(f'Candidate {s} failed; see {logs/s}.log')
            complete.add(s)
            print(json.dumps(dict(stage='candidates', complete=len(complete), total=len(symbols), symbol=s)), flush=True)
        for s in symbols:
            if s in complete or s in active or s in unavailable:
                continue
            output = SCREEN/f'{s}.json.gz'
            if output.exists():
                with gzip.open(output, 'rt') as stream:
                    old = json.load(stream)
                assert old['source_sha256'] == fingerprints() and old['policy_sha256'] == sha256(PROTOCOL.read_bytes()).hexdigest()
                complete.add(s)
                continue
            if len(active) >= workers or not ready_path(s).exists():
                continue
            stream = (logs/f'candidate_{s}.log').open('w')
            process = subprocess.Popen([sys.executable,'-u','-m','research.pool_screen',s], stdout=stream, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            active[s] = process, stream
        time.sleep(3)
    selection = select_pool(unavailable)
    jobs = [(f'later_{name}',policy['primary_equity'],path) for name in ('selected','all17','earlier_five','mcl_only') for path in policy['paths']]
    jobs += [('full_selected',initial,path) for initial in policy['full_period_equities'] for path in policy['paths']]
    def job(name, initial, path):
        with (logs/f'{name}_{initial}_{path}.log').open('w') as stream:
            subprocess.run([sys.executable,'-u','-m','research.pool_portfolio',name,'--equity',str(initial),'--path',path],
                           stdout=stream,stderr=subprocess.STDOUT,check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        return dict(name=name, initial=initial, path=path)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures=[pool.submit(job,*args) for args in jobs]
        for i,f in enumerate(as_completed(futures),1):
            print(json.dumps(dict(stage='portfolios', completed=i,total=len(jobs),**f.result())),flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers',type=int,default=3)
    main(parser.parse_args().workers)
