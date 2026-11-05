"""Equivalent active-position accounting for the larger research universe."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from hashlib import sha256
from heapq import merge
from itertools import groupby
from pathlib import Path
import argparse
import json
import subprocess
import sys
import time

from crt.models import INSTRUMENTS
from crt.strategy import trading_day
from research import universe65 as base
from research.pool_portfolio import ClockPortfolio
from research.portfolio_study import symbol_bars


class ActivePositions(dict):
    def __init__(self,owner,symbol):
        super().__init__();self.owner=owner;self.symbol=symbol

    def __setitem__(self,key,value):
        assert key==self.symbol
        existed=key in self
        super().__setitem__(key,value)
        if not existed:
            self.owner.active.append(key)
            self.owner.active.sort(key=self.owner.order.__getitem__)

    def pop(self,key,*default):
        existed=key in self
        value=super().pop(key,*default)
        if existed:self.owner.active.remove(key)
        return value

    def __delitem__(self,key):
        super().__delitem__(key);self.owner.active.remove(key)

    def clear(self):
        for key in list(self):self.pop(key)


class FastClockPortfolio(ClockPortfolio):
    def __init__(self,*args,**kwargs):
        self.active=[];self.order={}
        super().__init__(*args,**kwargs)
        self.order={s:i for i,s in enumerate(self.engines)}
        for s,e in self.engines.items():
            assert not e.positions
            e.positions=ActivePositions(self,s)

    @property
    def positions(self):
        return {s:self.engines[s].positions[s] for s in self.active}

    @property
    def equity(self):
        return self.cash+sum((self.engines[s].marks.get(s,p.entry)-p.entry)*p.setup.side*INSTRUMENTS[s].point_value*p.qty
                             for s in self.active for p in (self.engines[s].positions[s],))


def runtime_hashes():
    return {str(Path(__file__).resolve()):sha256(Path(__file__).read_bytes()).hexdigest()}


def run(name,path):
    file=base.PORTFOLIOS/f'{name}_{path}.json.gz'
    if file.exists():
        doc=base.read(file)
        assert doc['runtime_source_sha256']==runtime_hashes() and doc['source_sha256']==base.model_hashes()
        return
    base.ClockPortfolio=FastClockPortfolio
    base.portfolio(name,path)
    doc=base.read(file);doc['runtime_source_sha256']=runtime_hashes()
    base.write(file,doc)


def controls():
    base.register65();policy=base.policy()
    selection=json.loads(Path('research/results/pool_selection.json').read_text())
    basket=selection['selected'];ready={s:json.loads(base.ready_path(s).read_text()) for s in set(basket)|{'MNQ'}}
    capital=json.loads(Path('research/portfolio_protocol.json').read_text());costs=json.loads(Path('research/choch_breadth_protocol.json').read_text())
    checked=[]
    for path in policy['paths']:
        p=FastClockPortfolio(basket,50000,path,capital,costs,ready)
        for at,group in groupby(merge(*(symbol_bars(r) for r in ready.values()),key=lambda b:(b.start,b.symbol)),key=lambda b:b.start):
            if trading_day(at)>=policy['test_end_exclusive']:break
            p.batch(list(group),policy['selection_cutoff_exclusive'],policy['test_end_exclusive'])
        p.finish();actual=p.result()
        file=Path('research/results/pool_portfolios')/f'later_selected_50000_{path}.json.gz';old=base.read(file)
        assert actual['trades']==old['trades'] and actual['daily']==old['daily']
        checked.append(dict(path=path,trades=len(actual['trades']),daily_marks=len(actual['daily']),reference_sha256=sha256(file.read_bytes()).hexdigest()))
    doc=dict(status='passed',controls=checked,runtime_source_sha256=runtime_hashes(),base_source_sha256=base.model_hashes())
    Path('research/results/universe65_runtime_controls.json').write_text(json.dumps(doc,indent=2),encoding='utf-8')
    print(json.dumps(dict(stage='runtime_controls',status='passed',controls=checked)),flush=True)


def run_all(workers):
    base.register65();logs=Path('runs/universe65');logs.mkdir(parents=True,exist_ok=True)
    done=set();active={};unavailable={};symbols=base.universe()
    while len(done)+len(unavailable)<len(symbols):
        status=base.DATA/'status.json'
        if status.exists():unavailable=json.loads(status.read_text()).get('unavailable',{})
        for s,(process,stream) in list(active.items()):
            code=process.poll()
            if code is None:continue
            stream.close();active.pop(s)
            if code:raise RuntimeError(f'Candidate {s} failed: {logs}')
            done.add(s);print(json.dumps(dict(stage='candidates',complete=len(done),total=len(symbols),symbol=s)),flush=True)
        for s in symbols:
            if s in done or s in active or s in unavailable:continue
            file=base.CANDIDATES/f'{s}.json.gz'
            if file.exists():assert base.read(file)['source_sha256']==base.model_hashes();done.add(s);continue
            if len(active)>=workers or not base.ready_path(s).exists():continue
            stream=(logs/f'candidate_{s}.log').open('w')
            process=subprocess.Popen([sys.executable,'-u','-m','research.universe65','--screen',s],stdout=stream,stderr=subprocess.STDOUT,
                                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0));active[s]=process,stream
        time.sleep(3)
    base.select(unavailable)
    with (logs/'runtime_controls.log').open('w') as stream:
        subprocess.run([sys.executable,'-u','-m','research.universe65_fast','--controls'],stdout=stream,stderr=subprocess.STDOUT,
                       check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    print(json.dumps(dict(stage='runtime_controls',status='passed')),flush=True)
    def job(name,path):
        with (logs/f'{name}_{path}.log').open('w') as stream:
            subprocess.run([sys.executable,'-u','-m','research.universe65_fast','--portfolio',name,'--path',path],stdout=stream,
                           stderr=subprocess.STDOUT,check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        return dict(name=name,path=path)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs=[pool.submit(job,name,path) for name in base.policy()['portfolio_cases'] for path in base.policy()['paths']]
        for i,f in enumerate(as_completed(jobs),1):print(json.dumps(dict(stage='portfolios',complete=i,total=len(jobs),**f.result())),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--portfolio');parser.add_argument('--path',choices=['high_first','low_first'])
    parser.add_argument('--controls',action='store_true');parser.add_argument('--workers',type=int,default=3)
    args=parser.parse_args()
    if args.controls:controls()
    elif args.portfolio:run(args.portfolio,args.path)
    else:run_all(args.workers)
