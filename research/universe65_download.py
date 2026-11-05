"""Bounded stock downloads sharing the verified, paced REST client."""
from concurrent.futures import ThreadPoolExecutor,as_completed
import json

from research import download_breadth
from research.universe65 import DATA,PacedStockClient,policy,register65


def main():
    register65();DATA.mkdir(exist_ok=True);download_breadth.DEST=DATA
    # Recover only invalid interrupted cache files inside this dataset.
    quarantined=[]
    for file in (DATA/'raw').glob('*.json'):
        try:json.loads(file.read_text())
        except (json.JSONDecodeError,UnicodeDecodeError):
            assert DATA.resolve() in file.resolve().parents
            target=file.with_suffix('.incomplete')
            file.replace(target);quarantined.append(str(target))
    a=download_breadth.Acquisition();a.client=PacedStockClient()
    probe=policy()['rate_policy']['history_probe']
    try:
        doc=a.client.request(f'/v2/aggs/ticker/SPY/range/1/minute/{probe}/{probe}',{'limit':1,'adjusted':'true'})
        verified=bool(doc.get('results'))
    except Exception:
        verified=False
    a.client.interval=1 if verified else 13
    proof=dict(status='history_beyond_basic_verified' if verified else 'conservative_fallback',date=probe,
               interval=a.client.interval,stock_download_workers=3,quarantined_incomplete_files=quarantined,
               inference='Historical depth supports paid stocks access; paced shared client, not an account billing lookup.')
    (DATA/'access_probe.json').write_text(json.dumps(proof,indent=2))
    print(json.dumps(dict(stage='stock_access',**proof)),flush=True)
    unavailable={}
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs={pool.submit(a.run,s):s for s in policy()['added_symbols']}
        for f in as_completed(jobs):
            symbol=jobs[f]
            try:f.result()
            except Exception as exc:unavailable[symbol]=type(exc).__name__+': '+str(exc)
            (DATA/'status.json').write_text(json.dumps(dict(complete=False,unavailable=unavailable)))
    (DATA/'status.json').write_text(json.dumps(dict(complete=True,unavailable=unavailable)))


if __name__=='__main__':main()
