"""Opt-in 65-market dataset, unchanged screening, and shared-account comparisons."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor,as_completed
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
import time

from crt.data import read_bars
from crt.massive import MassiveError
from crt.strategy import trading_day
from research import download_breadth
from research.breadth_study import collect,config
from research.choch_study import replay
from research.expanded_markets import EXTRA,ExpandedEngine,Market,register
from research import pool_markets
from research.pool_select import training_stats,choose,fingerprints
from research.pool_portfolio import ClockPortfolio
from research.portfolio_study import symbol_bars

PROTOCOL=Path('research/universe65_protocol.json')
DATA=Path('data/choch_universe65')
CANDIDATES=Path('research/results/universe65_candidates')
PORTFOLIOS=Path('research/results/universe65_portfolios')
SELECTION=Path('research/results/universe65_selection.json')


def policy():return json.loads(PROTOCOL.read_text())


def model_hashes():
    paths=[PROTOCOL,Path(__file__).resolve(),Path('research/pool_protocol.json'),Path('research/portfolio_protocol.json'),Path('research/choch_breadth_protocol.json')]
    return fingerprints()|{str(p):sha256(p.read_bytes()).hexdigest() for p in paths}


def universe():return pool_markets.universe()+policy()['added_symbols']


def register65():
    pool_markets.register_pool()
    for s in policy()['added_symbols']:EXTRA[s]=Market(s,'stock',.01,1.,'US_STOCKS','stock',s)
    register()


def ready_path(symbol):
    return DATA/f'READY_{symbol}.json' if symbol in policy()['added_symbols'] else pool_markets.ready_path(symbol)


class PacedStockClient(download_breadth.MassiveClient):
    def request(self,path,params=None):
        try:return super().request(path,params)
        except MassiveError as exc:
            if '429' not in str(exc):raise
            self.interval=13
            return super().request(path,params)


def download():
    register65();DATA.mkdir(exist_ok=True)
    download_breadth.DEST=DATA
    a=download_breadth.Acquisition();a.client=PacedStockClient()
    proof=dict(status='not_verified',interval=13)
    try:
        probe=policy()['rate_policy']['history_probe']
        doc=a.client.request(f'/v2/aggs/ticker/SPY/range/1/minute/{probe}/{probe}',{'limit':1,'adjusted':'true'})
        if doc.get('results'):
            a.client.interval=policy()['rate_policy']['verified_paid_stock_interval_seconds']
            proof=dict(status='history_beyond_basic_verified',date=probe,results=len(doc['results']),interval=a.client.interval,
                       inference='Paid-stock-tier access, based on historical depth and official pricing; not a billing-account lookup.')
    except MassiveError:
        pass
    (DATA/'access_probe.json').write_text(json.dumps(proof,indent=2))
    print(json.dumps(dict(stage='stock_access',**proof)),flush=True)
    unavailable={}
    for symbol in policy()['added_symbols']:
        try:a.run(symbol)
        except Exception as exc:
            unavailable[symbol]=type(exc).__name__+': '+str(exc)
            print(json.dumps(dict(stage='unavailable',symbol=symbol,error=unavailable[symbol])),flush=True)
        (DATA/'status.json').write_text(json.dumps(dict(complete=False,unavailable=unavailable)))
    (DATA/'status.json').write_text(json.dumps(dict(complete=True,unavailable=unavailable)))


def read(file):
    with gzip.open(file,'rt') as stream:return json.load(stream)


def write(file,doc):
    file.parent.mkdir(exist_ok=True)
    with file.open('wb') as raw:
        with gzip.GzipFile(fileobj=raw,mode='wb',mtime=0) as stream:stream.write(json.dumps(doc,allow_nan=False).encode())


def screen(symbol):
    register65();file=CANDIDATES/f'{symbol}.json.gz';source=model_hashes()
    if file.exists():assert read(file)['source_sha256']==source;return
    ready=json.loads(ready_path(symbol).read_text())
    inputs=ready['input_sha256']|{ready['schedule_file']:ready['calendar_sha256']}
    for name,digest in inputs.items():assert sha256(Path(name).read_bytes()).hexdigest()==digest
    provenance={}
    if symbol not in policy()['added_symbols']:
        original=Path('research/results/pool_candidates')/f'{symbol}.json.gz';old=read(original)
        assert old['source_sha256']==fingerprints()
        cases=old['cases'];provenance=dict(reused_file=str(original),sha256=sha256(original.read_bytes()).hexdigest())
    else:
        costs=json.loads(Path('research/choch_breadth_protocol.json').read_text())
        bars=read_bars([r['file'] for r in ready['records']]);cases=[]
        for path in policy()['paths']:
            cfg=config(symbol,path,costs,ready['schedule_file'])
            e=ExpandedEngine(cfg,5,'retest_close',2.,costs)
            replay(e,bars,policy()['training_start'],policy()['test_end_exclusive'])
            _,full=collect(e,costs,symbol,5,'retest_close',path);cases.append(full)
    write(file,dict(symbol=symbol,cases=cases,input_sha256=inputs,source_sha256=source,provenance=provenance))
    print(json.dumps(dict(stage='candidate',symbol=symbol,reused=bool(provenance))),flush=True)


def selection_policy():
    p=deepcopy(json.loads(pool_markets.PROTOCOL.read_text()))
    p['exposure_buckets'].update(policy()['new_exposure_buckets'])
    return p


def select(unavailable):
    costs=json.loads(Path('research/choch_breadth_protocol.json').read_text());p=selection_policy();candidates=[]
    for symbol in universe():
        if symbol in unavailable:
            candidates.append(dict(symbol=symbol,eligible=False,score=0.,unavailable=unavailable[symbol]));continue
        doc=read(CANDIDATES/f'{symbol}.json.gz');assert doc['source_sha256']==model_hashes()
        paths={r['path']:training_stats(symbol,r['trades'],p,costs) for r in doc['cases']}
        candidates.append(dict(symbol=symbol,paths=paths,eligible=all(x['eligible'] for x in paths.values()),score=min(x['score'] for x in paths.values())))
    ready=[s for s in universe() if s not in unavailable]
    baskets=dict(all29={s:5 for s in pool_markets.universe() if s in ready},all65={s:5 for s in ready},
                 eligible65={c['symbol']:5 for c in candidates if c['eligible']},top5_65={s:5 for s in choose(candidates,p)})
    doc=dict(protocol=policy(),selection_policy=p,candidates=sorted(candidates,key=lambda c:(-c['score'],c['symbol'])),baskets=baskets,
             unavailable=unavailable,source_sha256=model_hashes())
    SELECTION.write_text(json.dumps(doc,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps(dict(stage='selection',counts={k:len(v) for k,v in baskets.items()},top5=baskets['top5_65'])),flush=True)
    return doc


def portfolio(name,path):
    register65();source=model_hashes();file=PORTFOLIOS/f'{name}_{path}.json.gz'
    if file.exists():assert read(file)['source_sha256']==source;return
    selected=json.loads(SELECTION.read_text());assert selected['source_sha256']==source
    basket=selected['baskets'][name];p=policy()
    ready={s:json.loads(ready_path(s).read_text()) for s in set(basket)|{'MNQ'}};inputs={}
    for r in ready.values():inputs.update(r['input_sha256']);inputs[r['schedule_file']]=r['calendar_sha256']
    for n,digest in inputs.items():assert sha256(Path(n).read_bytes()).hexdigest()==digest
    capital=json.loads(Path('research/portfolio_protocol.json').read_text());costs=json.loads(Path('research/choch_breadth_protocol.json').read_text())
    account=ClockPortfolio(basket,p['initial_equity'],path,capital,costs,ready)
    for at,group in groupby(merge(*(symbol_bars(r) for r in ready.values()),key=lambda b:(b.start,b.symbol)),key=lambda b:b.start):
        if trading_day(at)>=p['test_end_exclusive']:break
        account.batch(list(group),p['selection_cutoff_exclusive'],p['test_end_exclusive'])
    account.finish();result=account.result()
    result.update(name=name,path=path,basket=basket,source_sha256=source,input_sha256=inputs,selection_sha256=sha256(SELECTION.read_bytes()).hexdigest())
    write(file,result);print(json.dumps(dict(stage='portfolio',name=name,path=path,stats=result['stats'])),flush=True)


def run_all(workers):
    register65();logs=Path('runs/universe65');logs.mkdir(parents=True,exist_ok=True)
    done=set();active={};unavailable={};symbols=universe()
    while len(done)+len(unavailable)<len(symbols):
        status=DATA/'status.json'
        if status.exists():unavailable=json.loads(status.read_text()).get('unavailable',{})
        for s,(process,stream) in list(active.items()):
            code=process.poll()
            if code is None:continue
            stream.close();active.pop(s)
            if code:raise RuntimeError(f'Candidate {s} failed: {logs}')
            done.add(s);print(json.dumps(dict(stage='progress',candidates=len(done),planned=len(symbols),symbol=s)),flush=True)
        for s in symbols:
            if s in done or s in active or s in unavailable:continue
            file=CANDIDATES/f'{s}.json.gz'
            if file.exists():assert read(file)['source_sha256']==model_hashes();done.add(s);continue
            if len(active)>=workers or not ready_path(s).exists():continue
            stream=(logs/f'candidate_{s}.log').open('w')
            process=subprocess.Popen([sys.executable,'-u','-m','research.universe65','--screen',s],stdout=stream,stderr=subprocess.STDOUT,
                                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            active[s]=process,stream
        time.sleep(3)
    select(unavailable)
    def job(name,path):
        with (logs/f'{name}_{path}.log').open('w') as stream:
            subprocess.run([sys.executable,'-u','-m','research.universe65','--portfolio',name,'--path',path],stdout=stream,stderr=subprocess.STDOUT,
                           check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        return dict(name=name,path=path)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs=[pool.submit(job,name,path) for name in policy()['portfolio_cases'] for path in policy()['paths']]
        for i,f in enumerate(as_completed(jobs),1):print(json.dumps(dict(stage='portfolio_progress',complete=i,total=len(jobs),**f.result())),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download',action='store_true');parser.add_argument('--screen');parser.add_argument('--portfolio')
    parser.add_argument('--path',choices=['high_first','low_first']);parser.add_argument('--workers',type=int,default=3)
    args=parser.parse_args()
    if args.download:download()
    elif args.screen:screen(args.screen)
    elif args.portfolio:portfolio(args.portfolio,args.path)
    else:run_all(args.workers)
