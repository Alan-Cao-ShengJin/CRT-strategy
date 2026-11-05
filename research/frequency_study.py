"""Controlled changes to opportunity frequency with fixed portfolio risk."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from hashlib import sha256
from heapq import merge
from itertools import groupby
from pathlib import Path
import argparse
import gzip
import json
import subprocess
import sys

from crt.strategy import trading_day
from research.pool_markets import ready_path, register_pool
from research.pool_select import SELECTION, fingerprints
from research.pool_portfolio import ClockPortfolio
from research.portfolio_study import AssetEngine, symbol_bars

PROTOCOL=Path('research/frequency_protocol.json')
RESULTS=Path('research/results/frequency')


class FrequencyAsset(AssetEngine):
    def __init__(self,owner,cfg,timeframe,rules,repeat):
        self.repeat=repeat
        super().__init__(owner,cfg,timeframe,rules)

    def _learn(self,bar):
        if self.repeat:
            self.states[bar.symbol].selected.clear()
        super()._learn(bar)


def configure(cfg,kind,variant,policy):
    cfg=deepcopy(cfg)
    if variant['wider_hours']:
        cfg.sessions=({'new_york':policy['expanded_stock_window_ny']} if kind=='stock'
                      else policy['expanded_futures_windows_ny'])
    if variant['repeat_setups']:
        cfg.max_trades_per_day=policy['repeat_max_trades_per_asset_day']
    return cfg.validate()


def model_hashes():
    return fingerprints() | {str(Path(__file__).resolve()):sha256(Path(__file__).read_bytes()).hexdigest()}


def run(variant_name,path):
    register_pool()
    policy=json.loads(PROTOCOL.read_text());variant=policy['variants'][variant_name]
    source=model_hashes();digest=sha256(PROTOCOL.read_bytes()).hexdigest()
    RESULTS.mkdir(exist_ok=True)
    file=RESULTS/f'{variant_name}_{path}.json.gz'
    if file.exists():
        with gzip.open(file,'rt') as stream:old=json.load(stream)
        assert old['frequency_source_sha256']==source and old['frequency_policy_sha256']==digest
        return
    selection=json.loads(SELECTION.read_text())
    assert selection['source_sha256']==fingerprints()
    basket=selection['selected']
    ready={s:json.loads(ready_path(s).read_text()) for s in set(basket)|{'MNQ'}}
    inputs={}
    for doc in ready.values():
        inputs.update(doc['input_sha256']);inputs[doc['schedule_file']]=doc['calendar_sha256']
    for name,expected in inputs.items():assert sha256(Path(name).read_bytes()).hexdigest()==expected
    capital=json.loads(Path('research/portfolio_protocol.json').read_text())
    costs=json.loads(Path('research/choch_breadth_protocol.json').read_text())
    p=ClockPortfolio(basket,policy['initial_equity'],path,capital,costs,ready)
    for symbol,e in list(p.engines.items()):
        cfg=configure(e.config,e.market.kind,variant,policy)
        p.engines[symbol]=FrequencyAsset(p,cfg,basket[symbol],costs,variant['repeat_setups'])
    for at,group in groupby(merge(*(symbol_bars(r) for r in ready.values()),key=lambda b:(b.start,b.symbol)),key=lambda b:b.start):
        if trading_day(at)>=policy['end_exclusive']:break
        p.batch(list(group),policy['start'],policy['end_exclusive'])
    p.finish();result=p.result()
    result.update(variant=variant_name,path=path,basket=basket,start=policy['start'],end_exclusive=policy['end_exclusive'],
                  frequency_source_sha256=source,frequency_policy_sha256=digest,
                  selection_sha256=sha256(SELECTION.read_bytes()).hexdigest(),input_sha256=inputs,
                  configs={s:e.config.to_dict() for s,e in p.engines.items()})
    with file.open('wb') as raw:
        with gzip.GzipFile(fileobj=raw,mode='wb',mtime=0) as stream:stream.write(json.dumps(result,allow_nan=False).encode())
    print(json.dumps(dict(variant=variant_name,path=path,stats=result['stats'])),flush=True)


def run_all(workers):
    policy=json.loads(PROTOCOL.read_text())
    logs=Path('runs/frequency');logs.mkdir(parents=True,exist_ok=True)
    def job(name,path):
        with (logs/f'{name}_{path}.log').open('w') as stream:
            subprocess.run([sys.executable,'-u','-m','research.frequency_study','--variant',name,'--path',path],
                           stdout=stream,stderr=subprocess.STDOUT,check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        return dict(variant=name,path=path)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs=[pool.submit(job,name,path) for name in policy['variants'] for path in policy['paths']]
        for i,f in enumerate(as_completed(jobs),1):print(json.dumps(dict(completed=i,total=len(jobs),**f.result())),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--variant');parser.add_argument('--path',choices=['high_first','low_first'])
    parser.add_argument('--workers',type=int,default=3)
    args=parser.parse_args()
    if args.variant:run(args.variant,args.path)
    else:run_all(args.workers)
