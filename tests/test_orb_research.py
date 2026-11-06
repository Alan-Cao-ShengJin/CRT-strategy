"""Causality, share units and bounded data preparation for the ORB study."""
from dataclasses import replace
from datetime import date, timedelta
import gzip
import json

import pytest

from research.orb_features import Daily, Opening, Split, features, select, share_factor
from research.orb_data import read_s3_credentials, object_key, reduce_day, session_bounds


def history():
    days = [(date(2026, 7, 10)+timedelta(days=i)).isoformat() for i in range(15)]
    return days, [Daily(d, 12, 10, 11, 2000000, 100000) for d in days]


def test_opening_features_use_only_prior_sessions():
    dates, past = history()
    o = Opening('2026-08-03', 11, 12, 10.5, 11.5, 200000)
    base = features('A', o, past, dates)
    future = past+[Daily('2026-08-03', 999, 1, 5, 1e9, 1e9), Daily('2026-08-04', 1000, 1, 3, 1e9, 1e9)]
    assert features('A', o, future, dates) == base
    assert base['atr'] == 2 and base['relative_volume'] == 2
    assert base['lookback_end'] < base['day'] and base['stop_distance'] == .2


def test_splits_convert_both_price_and_volume_causally():
    dates, past = history()
    event = Split('2026-08-03', 1, 2)
    o = Opening('2026-08-03', 6, 6.5, 5.5, 6.25, 400000)
    r = features('A', o, past, dates, [event])
    assert r['atr'] == 1 and r['average_daily_volume'] == 4000000 and r['relative_volume'] == 2
    assert features('A', o, past, dates, [event, Split('2026-08-04', 1, 10)]) == r
    assert share_factor('2026-07-31', '2026-08-03', [Split('2026-08-03', 10, 1)]) == .1


def test_missing_or_unconfirmed_history_and_opening_fail_closed():
    dates, past = history();o = Opening('2026-08-03', 11, 12, 10, 11.5, 200000)
    assert features('A', o, past[:-1], dates) is None
    assert features('A', replace(o, minutes=4), past, dates) is None
    assert features('A', o, past[:-1]+[replace(past[-1], valid=False)], dates) is None
    with pytest.raises(ValueError):
        features('A', o, past, dates[:-1]+[o.day])


def test_selection_is_deterministic_and_control_does_not_require_relative_volume():
    dates, past = history();o=Opening('2026-08-03',11,12,10,11.5,200000)
    a = features('A',o,past,dates);b = features('B',o,past,dates)
    c = dict(a,ticker='C',relative_volume=.5,average_daily_volume=3000000)
    assert [r['ticker'] for r in select([b,c,a],maximum=2)] == ['A','B']
    assert [r['ticker'] for r in select([b,c,a],maximum=2,mode='prior_volume')] == ['C','A']
    assert select([dict(a,side=0)]) == []
    with pytest.raises(ValueError,match='Duplicate'):
        select([a,a])


def test_credentials_are_explicit_and_api_key_cannot_substitute(tmp_path, monkeypatch):
    for name in ['MASSIVE_S3_ACCESS_KEY_ID','MASSIVE_S3_SECRET_ACCESS_KEY']:
        monkeypatch.delenv(name,raising=False)
    f=tmp_path/'private.env';f.write_text('MASSIVE_API_KEY=not-s3\nAWS_ACCESS_KEY_ID=unrelated\nAWS_SECRET_ACCESS_KEY=unrelated\n')
    with pytest.raises(ValueError):read_s3_credentials(f)
    f.write_text('MASSIVE_S3_ACCESS_KEY_ID=test-id\nMASSIVE_S3_SECRET_ACCESS_KEY=test-secret\n')
    assert read_s3_credentials(f)==('test-id','test-secret')
    assert object_key('2026-08-03')=='us_stocks_sip/minute_aggs_v1/2026/08/2026-08-03.csv.gz'
    with pytest.raises(ValueError):object_key('../private')


def test_flat_file_reduction_and_duplicate_quarantine(tmp_path):
    pytest.importorskip('duckdb');pq=pytest.importorskip('pyarrow.parquet')
    opened,closed=session_bounds('2026-08-03')
    rows=['ticker,volume,open,close,high,low,window_start,transactions']
    for i in range(7):rows.append(f'AAPL,100,10,10.5,11,9,{opened+i*60000000000},5')
    rows += [f'BAD,100,10,10.5,11,9,{opened},5']*2
    rows.append(f'AAPL,1000,10,10.5,11,9,{closed},5')  # outside regular session
    source=tmp_path/'source.csv.gz'
    with gzip.open(source,'wt') as f:f.write('\n'.join(rows)+'\n')
    out=tmp_path/'derived';r=reduce_day(source,'2026-08-03',out)
    assert r['sample_only'] and r['daily_tickers']==1 and r['rejected_duplicate_ticker_days']==1
    opening=pq.read_table(out/'opening.parquet').to_pylist()[0]
    daily=pq.read_table(out/'daily.parquet').to_pylist()[0]
    assert opening['minutes']==5 and opening['volume']==500
    assert daily['minutes']==7 and daily['volume']==700
    assert source.exists()  # reducers never remove caller-owned input


def test_half_day_and_non_session_boundaries():
    a,b=session_bounds('2025-11-28')
    assert (b-a)//60000000000==210
    with pytest.raises(ValueError):session_bounds('2026-08-02')


def test_null_prices_are_rejected_before_writing_summaries(tmp_path):
    pytest.importorskip('duckdb');pytest.importorskip('pyarrow')
    opened,_=session_bounds('2026-08-03')
    source=tmp_path/'invalid.csv.gz'
    with gzip.open(source,'wt') as f:
        f.write('ticker,volume,open,close,high,low,window_start,transactions\n')
        f.write(f'AAPL,100,,10.5,11,9,{opened},5\n')
    with pytest.raises(ValueError,match='invalid'):
        reduce_day(source,'2026-08-03',tmp_path/'derived')
    assert not list((tmp_path/'derived').glob('*.parquet'))
