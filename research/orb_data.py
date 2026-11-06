"""Bounded Massive S3 access and flat-file reduction for ORB research.

This prepares data and metadata only. It does not calculate strategy returns.
"""
import argparse
from datetime import date, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

from crt.massive import MassiveClient

ROOT = Path('data/orb_stocks_in_play')
PROTOCOL = Path('research/orb_research_protocol.json')
ENDPOINT = 'https://files.massive.com'
BUCKET = 'flatfiles'


def file_sha256(path):
    digest = sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_s3_credentials(env_file):
    names = ('MASSIVE_S3_ACCESS_KEY_ID', 'MASSIVE_S3_SECRET_ACCESS_KEY')
    values = {k: os.environ.get(k, '').strip() for k in names}
    file = Path(env_file)
    if file.exists():
        for line in file.read_text(encoding='utf-8-sig').splitlines():
            key, sep, value = line.partition('=')
            key = key.strip().removeprefix('export ')
            if sep and key in names and not values[key]:
                values[key] = value.strip().strip('\"\'')
    if not all(values.values()) or any(v.startswith('your_') for v in values.values()):
        raise ValueError('Set explicit MASSIVE_S3_ACCESS_KEY_ID and MASSIVE_S3_SECRET_ACCESS_KEY in the chosen local env file')
    return values[names[0]], values[names[1]]


def client(env_file):
    import boto3
    from botocore.config import Config
    key, secret = read_s3_credentials(env_file)
    return boto3.Session(aws_access_key_id=key, aws_secret_access_key=secret).client(
        's3', endpoint_url=ENDPOINT, region_name='us-east-1',
        config=Config(signature_version='s3v4', retries={'max_attempts': 3},
                      connect_timeout=15, read_timeout=60))


def object_key(day):
    d = date.fromisoformat(day)
    return f'us_stocks_sip/minute_aggs_v1/{d.year}/{d.month:02}/{day}.csv.gz'


def session_bounds(day):
    import exchange_calendars as xcals
    cal = xcals.get_calendar('XNYS', start=day, end=(date.fromisoformat(day)+timedelta(days=7)).isoformat())
    if not cal.is_session(day):
        raise ValueError('Not an exchange trading date')
    return int(cal.session_open(day).value), int(cal.session_close(day).value)


def reduce_day(source, day, destination, sample_symbols=('AAPL', 'MSFT', 'NVDA')):
    """Keep compact all-market daily/opening summaries and a labelled sample.

    Sample minutes verify parsing only; they are NOT the ranked ORB trade universe.
    Source must have independently verified as-traded share units before features
    or trading results are calculated.
    """
    import duckdb
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    opened, closed = session_bounds(day)
    con = duckdb.connect()
    try:
        con.execute("SET memory_limit='768MB'")
        con.execute("SET max_temp_directory_size='512MB'")
        scratch = destination/'duckdb_scratch'; scratch.mkdir()
        con.execute('SET temp_directory = ?', [str(scratch)])
        con.execute("""CREATE TABLE bars AS SELECT * FROM read_csv(?, header=true,
          columns={'ticker':'VARCHAR','volume':'DOUBLE','open':'DOUBLE','close':'DOUBLE',
                   'high':'DOUBLE','low':'DOUBLE','window_start':'BIGINT','transactions':'BIGINT'})
          WHERE window_start >= ? AND window_start < ?""", [str(source), opened, closed])
        total = con.execute('SELECT count(*) FROM bars').fetchone()[0]
        bad = con.execute("""SELECT count(*) FROM bars WHERE ticker IS NULL OR open IS NULL
          OR close IS NULL OR high IS NULL OR low IS NULL OR volume IS NULL OR window_start IS NULL
          OR NOT (isfinite(open) AND isfinite(close) AND isfinite(high) AND isfinite(low) AND isfinite(volume))
          OR least(open,close,high,low)<=0 OR volume<0 OR low>least(open,close)
          OR high<greatest(open,close) OR window_start % 60000000000 != 0""").fetchone()[0]
        if not total or bad:
            raise ValueError('Empty or invalid regular-session flat-file data')
        con.execute('''CREATE TABLE rejected AS SELECT ticker FROM bars GROUP BY ticker
                       HAVING count(*) != count(DISTINCT window_start)''')
        rejected = con.execute('SELECT count(*) FROM rejected').fetchone()[0]
        con.execute('DELETE FROM bars WHERE ticker IN (SELECT ticker FROM rejected)')
        daily_query = '''SELECT ticker, arg_min(open,window_start) AS open,
          max(high) AS high, min(low) AS low, arg_max(close,window_start) AS close,
          sum(volume) AS volume, count(*) AS minutes,
          min(window_start) AS first_ns, max(window_start) AS last_ns
          FROM bars GROUP BY ticker ORDER BY ticker'''
        opening_query = '''SELECT ticker, arg_min(open,window_start) AS open,
          max(high) AS high,min(low) AS low,arg_max(close,window_start) AS close,
          sum(volume) AS volume,count(*) AS minutes,
          min(window_start) AS first_ns,max(window_start) AS last_ns
          FROM bars WHERE window_start < ? GROUP BY ticker ORDER BY ticker'''
        daily = con.execute(daily_query).to_arrow_table()
        opening = con.execute(opening_query, [opened+5*60*10**9]).to_arrow_table()
        sample = con.execute('SELECT * FROM bars WHERE ticker IN (SELECT unnest(?)) ORDER BY ticker,window_start', [list(sample_symbols)]).to_arrow_table()
        if not daily.num_rows:
            raise ValueError('Every ticker-day failed source validation')
        import pyarrow.parquet as pq
        for name, table in [('daily', daily), ('opening', opening), ('sample_minutes', sample)]:
            pq.write_table(table, destination/(name+'.parquet'), compression='zstd')
        return dict(day=day, regular_open_ns=opened, regular_close_ns=closed,
                    input_regular_minutes=total, rejected_duplicate_ticker_days=rejected,
                    daily_tickers=daily.num_rows, opening_tickers=opening.num_rows,
                    sample_symbols=list(sample_symbols), sample_only=True,
                    price_basis_status='unverified_until_unadjusted_REST_crosscheck',
                    source_sha256=file_sha256(source),
                    output_sha256={p.name: sha256(p.read_bytes()).hexdigest() for p in destination.glob('*.parquet')})
    finally:
        con.close()


def fetch_one(day, env_file):
    day = date.fromisoformat(day).isoformat()
    rules = json.loads(PROTOCOL.read_text()); budget = rules['data_budget']
    ROOT.mkdir(parents=True, exist_ok=True)
    resolved_root = ROOT.resolve(strict=True)
    destination = ROOT/'days'/day
    if destination.exists():
        marker = destination/'manifest.json'
        if not marker.exists():
            raise FileExistsError('Incomplete extraction exists; inspect it before retrying')
        previous = json.loads(marker.read_text())
        if previous.get('protocol_sha256') != file_sha256(PROTOCOL) or previous.get('code_sha256') != file_sha256(__file__):
            raise ValueError('Extraction checkpoint uses different code or protocol')
        for name, digest in previous['output_sha256'].items():
            file = (destination/name).resolve(strict=True)
            if file.parent != destination.resolve(strict=True) or file_sha256(file) != digest:
                raise ValueError('Changed extraction checkpoint')
        return previous
    total = sum(p.stat().st_size for p in ROOT.rglob('*') if p.is_file() and not p.is_symlink())
    if total >= budget['maximum_workspace_bytes'] or shutil.disk_usage(resolved_root).free < budget['minimum_free_bytes']:
        raise RuntimeError('ORB research disk budget reached')
    s3 = client(env_file); key = object_key(day)
    info = s3.head_object(Bucket=BUCKET, Key=key)
    size = int(info['ContentLength'])
    if size > budget['maximum_raw_object_bytes'] or total+size+512*1024**2 > budget['maximum_workspace_bytes']:
        raise RuntimeError('Raw object exceeds the declared disk budget')
    scratch = ROOT/'transient'; scratch.mkdir(exist_ok=True)
    raw = (scratch/f'{day}_{uuid4().hex}.csv.gz').resolve()
    assert raw.parent == scratch.resolve() and raw.is_relative_to(resolved_root)
    if raw.exists():
        raise FileExistsError('Scratch filename collision')
    s3.download_file(BUCKET, key, str(raw))
    if raw.stat().st_size != size:
        raise RuntimeError('S3 download size mismatch; scratch retained for diagnosis')
    result = reduce_day(raw, day, ROOT/'days'/day)
    result.update(object_key=key, source_bytes=size, source_etag=str(info.get('ETag', '')).strip('"'),
                  protocol_sha256=sha256(PROTOCOL.read_bytes()).hexdigest(),
                  code_sha256=sha256(Path(__file__).read_bytes()).hexdigest())
    (ROOT/'days'/day/'manifest.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    # Remove only this invocation's exact scratch object after durable output.
    assert raw.parent == scratch.resolve() and raw.is_relative_to(resolved_root) and raw.is_file()
    raw.unlink()
    print(json.dumps({k: result[k] for k in ('day','source_bytes','daily_tickers','opening_tickers','sample_only')}), flush=True)
    return result


def validate_basis(day):
    """Compare sampled flat-file bars to REST adjusted=false, without S3 keys.

    This is a sample check, not proof of complete source agreement. Include a
    pre-split date when testing whether the feed is as-traded or back-adjusted.
    """
    import pyarrow.parquet as pq
    day = date.fromisoformat(day).isoformat()
    directory = ROOT/'days'/day
    rows = pq.read_table(directory/'sample_minutes.parquet').to_pylist()
    c = MassiveClient(); checks = []
    for symbol in sorted({r['ticker'] for r in rows}):
        sample = [r for r in rows if r['ticker'] == symbol][:3]
        start, end = sample[0]['window_start']//1000000, sample[-1]['window_start']//1000000+60000
        doc = c.request(f'/v2/aggs/ticker/{symbol}/range/1/minute/{start}/{end-1}',
                        {'adjusted':'false','sort':'asc','limit':50000})
        reference = {int(r['t'])*1000000:r for r in doc.get('results', [])}
        agree = all(r['window_start'] in reference and
                    all(abs(r[k]-reference[r['window_start']][field]) <= 1e-6
                        for k, field in [('open','o'),('high','h'),('low','l'),('close','c'),('volume','v')]) for r in sample)
        checks.append(dict(symbol=symbol, minutes=len(sample), matches_unadjusted_rest=agree))
    result = dict(day=day, status='sample_matches_unadjusted_REST' if checks and all(r['matches_unadjusted_rest'] for r in checks) else 'basis_or_source_disagreement',
                  checks=checks, sample_only=True)
    (directory/'basis_check.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result), flush=True)
    return result


def prepare_universe():
    """Historical month-end membership, not today's surviving ticker list."""
    rules = json.loads(PROTOCOL.read_text())
    first, end = date.fromisoformat(rules['pilot_start']), date.fromisoformat(rules['pilot_end_exclusive'])
    out = ROOT/'universe'; out.mkdir(parents=True, exist_ok=True)
    c = MassiveClient()
    proof = Path('data/choch_universe65/access_probe.json')
    if proof.exists() and json.loads(proof.read_text()).get('status') == 'history_beyond_basic_verified':
        c.interval = 1
    month = first.replace(day=1)
    summary = []
    while month < end:
        at = month-timedelta(days=1); destination = out/(str(month)+'.json')
        if destination.exists():
            doc = json.loads(destination.read_text())
        else:
            rows = {}
            for exchange in ('XNYS','XNAS'):
                for r in c.pages('/v3/reference/tickers', {'market':'stocks','type':'CS',
                         'locale':'us','date':str(at),'active':'true','exchange':exchange,'limit':1000}):
                    if r.get('type') != 'CS' or r.get('primary_exchange') != exchange:
                        raise RuntimeError('Unexpected historical ticker classification')
                    rows[r['ticker']] = {k:r.get(k) for k in ('ticker','primary_exchange','type','active','cik','composite_figi')}
            if not rows:
                raise RuntimeError('Historical membership unavailable')
            doc = dict(effective_month=str(month), snapshot_date=str(at), members=[rows[k] for k in sorted(rows)])
            destination.write_text(json.dumps(doc), encoding='utf-8')
        summary.append(dict(month=str(month), snapshot=str(at), common_stocks=len(doc['members']),
                            sha256=sha256(destination.read_bytes()).hexdigest()))
        print(json.dumps(summary[-1]), flush=True)
        month = (month.replace(day=28)+timedelta(days=4)).replace(day=1)
    (out/'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prepare-universe', action='store_true')
    p.add_argument('--fetch-one', metavar='YYYY-MM-DD')
    p.add_argument('--validate-basis', metavar='YYYY-MM-DD')
    p.add_argument('--env-file', default='.env.research-data')
    args = p.parse_args()
    if args.prepare_universe:
        prepare_universe()
    if args.fetch_one:
        try:
            fetch_one(args.fetch_one, args.env_file)
        except Exception as exc:
            # Avoid SDK exception text, request headers, URLs or credential values.
            print(json.dumps(dict(status='failed', error_type=type(exc).__name__,
                action='Verify explicit Massive S3 credentials, entitlement, date and local research outputs.')), flush=True)
            raise SystemExit(1) from None
    if args.validate_basis:
        validate_basis(args.validate_basis)
