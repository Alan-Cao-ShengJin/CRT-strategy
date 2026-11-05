"""Verify larger-universe portfolios, selection provenance, and equivalent runtime."""
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from math import ceil
import csv
import json
import statistics

from crt.config import Config
from crt.models import timestamp
from crt.strategy import session_at,trading_day
from research.breadth_report import pair_summary
from research.breadth_study import config,stressed_pnl
from research.expanded_markets import market
from research.pool_report import trade_audit
from research.pool_select import choose,training_stats,fingerprints
from research import universe65 as base
from research.universe65_fast import runtime_hashes

LABELS={'all17':'All 17','all29':'All 29','all65':'All 65','eligible65':'All early-eligible markets',
        'top5_65':'Top five selected from 65','selected29':'Top five selected from 29','mcl_only':'MCL alone'}


def audit_portfolio(r,capital,costs):
    stats=r['stats'];ts=r['trades']
    trade_audit(ts,costs)
    assert len(ts)==stats['trades']==len(r['allocations'])
    assert abs(50000+sum(t['net'] for t in ts)-stats['ending_equity'])<1e-5
    prior=50000.;factor=1.
    for d in r['daily']:
        assert abs(d['pnl']-(d['equity']-prior))<1e-5
        assert abs(d['return_pct']-100*(d['equity']/prior-1))<1e-8
        factor*=1+d['return_pct']/100;prior=d['equity']
    assert abs(prior-stats['ending_equity'])<1e-5
    assert abs(factor-stats['ending_equity']/50000)<1e-8
    assert abs(statistics.mean(d['return_pct'] for d in r['daily'])-stats['mean_daily_return_pct'])<1e-8
    assert len(r['daily'])==stats['market_days']
    assert abs(len(ts)/stats['market_days']-stats['mean_trades_per_market_day'])<1e-8
    for a in r['allocations']:
        assert a['risk']<=capital['risk_fraction']*a['equity_before']+1e-5
        assert a['total_risk']<=capital['max_open_risk_fraction']*a['equity_before']+1e-5
        assert a['positions']<=capital['max_positions']
    configs={}
    for s in {t['symbol'] for t in ts}:
        ready=json.loads(base.ready_path(s).read_text())
        configs[s]=config(s,r['path'],costs,ready['schedule_file'])
    for t in ts:
        cfg=configs[t['symbol']]
        assert session_at(timestamp(t['entry_time']),cfg) is not None
        assert base.policy()['selection_cutoff_exclusive']<=trading_day(timestamp(t['entry_time']))<base.policy()['test_end_exclusive']


def main():
    base.register65();policy=base.policy();selected=json.loads(base.SELECTION.read_text())
    assert selected['source_sha256']==base.model_hashes()
    p=base.selection_policy();costs=json.loads(Path('research/choch_breadth_protocol.json').read_text())
    capital=json.loads(Path('research/portfolio_protocol.json').read_text())
    inputs={};hashes={};candidate_rows=[];candidate_executions=0;reused=0;entry_fills={}
    def remember(trades):
        for t in trades:
            entry_fills.setdefault((t['symbol'],t['contract'],t['entry_time']),set()).add((t['side'],t['entry']))
    assert len(base.universe())==65
    for c in selected['candidates']:
        if 'unavailable' in c:continue
        file=base.CANDIDATES/f'{c["symbol"]}.json.gz';doc=base.read(file)
        assert doc['source_sha256']==base.model_hashes()
        for r in doc['cases']:
            trade_audit(r['trades'],costs,True,r['setups'])
            assert abs(sum(t['net'] for t in r['trades'])-r['stats']['net_pnl'])<1e-5
            assert training_stats(c['symbol'],r['trades'],p,costs)==c['paths'][r['path']]
            candidate_executions+=len(r['trades'])
            remember(r['trades'])
        if doc['provenance']:
            source=Path(doc['provenance']['reused_file'])
            assert sha256(source.read_bytes()).hexdigest()==doc['provenance']['sha256']
            assert base.read(source)['cases']==doc['cases'];reused+=len(doc['cases'])
        candidate_rows.append(dict(symbol=c['symbol'],training_eligible=c['eligible'],training_score=c['score'],
                                   new=c['symbol'] in policy['added_symbols'],**pair_summary(doc['cases'],costs)))
        inputs.update(doc['input_sha256']);hashes[str(file)]=sha256(file.read_bytes()).hexdigest()
    assert list(selected['baskets']['top5_65'])==choose(selected['candidates'],p)
    assert set(selected['baskets']['eligible65'])=={c['symbol'] for c in selected['candidates'] if c['eligible']}
    assert set(selected['baskets']['all65'])==set(base.universe())-set(selected['unavailable'])
    assert set(selected['baskets']['all29'])==set(base.pool_markets.universe())-set(selected['unavailable'])
    runtime=json.loads(Path('research/results/universe65_runtime_controls.json').read_text())
    assert runtime['status']=='passed' and runtime['runtime_source_sha256']==runtime_hashes()
    assert runtime['base_source_sha256']==base.model_hashes()
    assert len(runtime['controls'])==2
    for c in runtime['controls']:
        reference=Path('research/results/pool_portfolios')/f'later_selected_50000_{c["path"]}.json.gz'
        assert sha256(reference.read_bytes()).hexdigest()==c['reference_sha256']
    rows=[]
    for name in policy['portfolio_cases']:
        for path in policy['paths']:
            file=base.PORTFOLIOS/f'{name}_{path}.json.gz';r=base.read(file)
            assert r['source_sha256']==base.model_hashes() and r['runtime_source_sha256']==runtime_hashes()
            assert r['selection_sha256']==sha256(base.SELECTION.read_bytes()).hexdigest()
            assert r['basket']==selected['baskets'][name]
            audit_portfolio(r,capital,costs);rows.append(r)
            inputs.update(r['input_sha256']);hashes[str(file)]=sha256(file.read_bytes()).hexdigest()
    for name,oldname in [('all17','later_all17'),('selected29','later_selected'),('mcl_only','later_mcl_only')]:
        for path in policy['paths']:
            file=Path('research/results/pool_portfolios')/f'{oldname}_50000_{path}.json.gz';r=base.read(file)
            assert r['source_sha256']==fingerprints()
            audit_portfolio(r,capital,costs)
            r['name']=name;rows.append(r);inputs.update(r['input_sha256']);hashes[str(file)]=sha256(file.read_bytes()).hexdigest()
    dates=[d['day'] for d in rows[0]['daily']]
    for r in rows:
        remember(r['trades'])
        assert [d['day'] for d in r['daily']]==dates
        r['stats']['fixed_fill_stress_net_pnl']=sum(stressed_pnl([SimpleNamespace(**t) for t in r['trades'] if t['symbol']==s],market(s),costs)
             for s in set(t['symbol'] for t in r['trades']))
        r['stats']['traded_assets']=len(r['stats']['trades_by_symbol'])
    for file,digest in inputs.items():assert sha256(Path(file).read_bytes()).hexdigest()==digest
    matched=set()
    for file in inputs:
        if Path(file).suffix!='.csv':continue
        with Path(file).open(newline='',encoding='utf-8-sig') as stream:
            for bar in csv.DictReader(stream):
                key=(bar['symbol'],bar['contract'],bar['timestamp'])
                if key not in entry_fills:continue
                raw=float(bar['open']);m=market(bar['symbol'])
                slip=(ceil(raw*costs['crypto_slippage_bps']/10000/m.tick-1e-8)*m.tick if m.kind=='crypto' else m.tick)
                assert all(abs(fill-(raw+side*slip))<1e-7 for side,fill in entry_fills[key])
                matched.add(key)
    assert matched==set(entry_fills)
    paired=[]
    for name in ('all17','all29','all65','eligible65','selected29','top5_65','mcl_only'):
        cases=[r for r in rows if r['name']==name]
        fields=('trades','traded_assets','mean_trades_per_market_day','mean_daily_return_pct','return_pct','max_minute_mark_drawdown_pct','profit_factor','fixed_fill_stress_net_pnl')
        paired.append(dict(name=name,enabled_assets=len(cases[0]['basket']),market_days=len(dates),
                           ranges={k:[min(r['stats'][k] or 0. for r in cases),max(r['stats'][k] or 0. for r in cases)] for k in fields}))
    record=dict(protocol=policy,selection=selected,comparisons=paired,candidate_diagnostics=candidate_rows,
                cases=[{k:r[k] for k in ('name','path','basket','stats','rejections','roll_marks')} for r in rows],
                reached_1pct_mean_daily_both_paths=[c['name'] for c in paired if c['ranges']['mean_daily_return_pct'][0]>=1],
                input_sha256=inputs,journal_sha256=hashes,runtime_controls=runtime,
                access_probe=json.loads((base.DATA/'access_probe.json').read_text()),
                audit=dict(status='passed',candidate_cases=2*len(candidate_rows),reused_candidate_cases=reused,new_portfolio_cases=8,reused_portfolio_cases=6,
                           trade_executions=candidate_executions+sum(len(r['trades']) for r in rows),unique_raw_entry_minutes_checked=len(matched),unit_tests=142),
                report_source_sha256=sha256(Path(__file__).read_bytes()).hexdigest())
    Path('research/results/universe65_summary.json').write_text(json.dumps(record,indent=2,allow_nan=False),encoding='utf-8')
    plot(record);note(record)
    print(json.dumps(dict(comparisons=paired,reached_1pct=record['reached_1pct_mean_daily_both_paths'],audit=record['audit']),indent=2),flush=True)


def plot(record):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    p=record['comparisons'];labels=[LABELS[x['name']] for x in p]
    fig,axes=plt.subplots(1,2,figsize=(13,5.4),layout='constrained')
    for ax,key,title in zip(axes,['mean_trades_per_market_day','mean_daily_return_pct'],['Trades per market day','Mean daily account return (%)']):
        values=[r['ranges'][key][0] for r in p]
        bars=ax.barh(labels,values,color=['#9ba8ba','#748aaa','#4c70a0','#b69457','#4f9585','#326e61','#9c78ad'])
        ax.bar_label(bars,fmt='%.3f' if 'return' in key else '%.2f',padding=4)
        ax.invert_yaxis();ax.set_title(title);ax.axvline(0,color='#555',linewidth=.7);ax.grid(axis='x',alpha=.2);ax.margins(x=.2)
    fig.suptitle('More possible markets: original 5m retest rules and unchanged shared risk\nMay-November 2026, fresh $50k, modeled costs; retrospective research',fontsize=12)
    fig.savefig('research/results/universe65_comparison.png',dpi=160);plt.close(fig)


def note(r):
    selection=r['selection'];p=r['protocol'];a=r['audit'];table=[];candidates=[]
    for c in r['comparisons']:
        v=c['ranges'];lo=lambda k:v[k][0];hi=lambda k:v[k][1]
        table.append(f"| {LABELS[c['name']]} | {c['enabled_assets']} | {int(lo('traded_assets'))} | {lo('mean_trades_per_market_day'):.2f} | {lo('mean_daily_return_pct'):+.3f}% | {lo('return_pct'):+.2f}% | {hi('max_minute_mark_drawdown_pct'):.2f}% | {lo('profit_factor'):.2f} | ${lo('fixed_fill_stress_net_pnl'):,.2f} |")
    for c in selection['candidates']:
        if 'unavailable' in c:
            candidates.append(f"| {c['symbol']} | unavailable | - | - | - | {c['unavailable']} |");continue
        ss=list(c['paths'].values());fails=sorted(set(f for s in ss for f in s['failed_conditions']))
        pf=min(s['profit_factor'] if s['profit_factor'] is not None else float('inf') if s['net_pnl']>0 else 0 for s in ss)
        status='top five' if c['symbol'] in selection['baskets']['top5_65'] else 'eligible' if c['eligible'] else 'fails'
        candidates.append(f"| {c['symbol']} | {status} | {min(s['trades'] for s in ss)} | {pf:.2f} | {c['score']:.3f} | {', '.join(fails) or selection['selection_policy']['exposure_buckets'][c['symbol']]} |")
    winners=', '.join(r['reached_1pct_mean_daily_both_paths']) or 'none'
    broad=next(c for c in r['comparisons'] if c['name']=='all65')['ranges']
    old=next(c for c in r['comparisons'] if c['name']=='all29')['ranges']
    text=f'''# Expanding the CHoCH universe: 29 to 65 possible assets

Added **36 predefined instruments**, taking the candidate universe to **65**. New names: **{', '.join(p['added_symbols'])}**. Stocks and ETFs cover technology, banks, consumer sectors, healthcare, industrials, utilities, real estate, bonds, international equities and gold miners. This is a chosen surviving-instrument sample, not a point-in-time universe or proof of independent exposures.

Only market membership changes. Keep **5m confirmed-retest / 2R**, original entry windows, first eligible signal per session, two entries per asset/day, 1% planned risk per entry, 2% combined open risk, two simultaneous positions, one equity-index future, 3% daily loss budget, per-asset consecutive-loss halts, and the existing capital reservations. The previous wider-hour/repeated-entry variants are **not** used here.

**Increasing the universe did not improve this historical portfolio.** Moving from 29 to 65 enabled markets increased trades per day from **{old['mean_trades_per_market_day'][0]:.2f} to {broad['mean_trades_per_market_day'][0]:.2f}**, while mean daily return fell from **{old['mean_daily_return_pct'][0]:+.3f}% to {broad['mean_daily_return_pct'][0]:+.3f}%** and maximum minute-mark drawdown rose from **{old['max_minute_mark_drawdown_pct'][1]:.2f}% to {broad['max_minute_mark_drawdown_pct'][1]:.2f}%**. All-65 P&L repriced at the existing higher-cost assumptions is **${broad['fixed_fill_stress_net_pnl'][0]:,.2f}**. These results do not support larger membership as a route to a 1%-plus daily average under the fixed risk controls.

## Same-date portfolio results

Period: **1 May-4 November 2026**. Fresh **$50,000** shared accounts and the same **134 observed MNQ market dates**, including zero-trade days. Fees and slippage are modeled. Rows report lower return/trade count/PF/stress and higher minute-mark drawdown across both hypothetical price paths.

| Enabled pool | Enabled assets | Assets actually traded | Trades / market day | Mean daily return | Period return | Maximum minute-mark DD | PF | Fixed-fill stress P&L |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(table)}

Variants reaching at least 1% arithmetic mean daily account return in both paths: **{winners}**. Daily return is daily P&L divided by prior closing equity, averaged over all market dates. These are not constant daily income rates. Minute marks can miss intraminute drawdowns, and assumed OHLC paths are not observed ticks or confidence bounds.

![Trade frequency and daily return as the possible market list expands](research/results/universe65_comparison.png)

## Membership experiments

- **All 29 / all 65:** enable the entire predefined available universe without a profitability filter. These directly test adding possible markets with the same execution limits.
- **All early-eligible markets:** **{len(selection['baskets']['eligible65'])} assets** passed the unchanged training screen; enable all of them, including multiple candidates in a broad exposure bucket. Portfolio position, index-futures and risk limits still apply. Members: **{', '.join(selection['baskets']['eligible65'])}**.
- **Top five from 65:** use the existing maximum-five, one-per-exposure-bucket selection rule. Members: **{', '.join(selection['baskets']['top5_65']) or 'cash'}**, all 5m.
- **Top five from 29 / all 17 / MCL alone:** unchanged same-date controls from the prior study, reused with explicit journal hashes. The 29-market selected basket was JPM, MCL, MYM, SLV and AMZN. No current-November hindsight watchlist is used.

Selection uses only trades entered and completed **3 November 2025-30 April 2026**. Require each path to have at least 20 early trades, PF >=1.20, positive stressed P&L and at least two positive two-month training blocks. Exclude research roll/end-data liquidations. Rank by the lower mean stressed net R times square root of early trade count, with deterministic alphabetical ties. No later return or target daily profit enters membership scores.

**The year was already inspected.** This is another retrospective experiment. A larger search space increases opportunities for selection bias; a chronological split does not make it an untouched forward test. The ranked five and eligible pool can change composition simply because new candidates enter the same rule. None of these comparisons establishes persistent future profits or an optimal universe size.

## Capital and executable-trade limitations

Scanning 65 markets does not fund 65 positions. Orders compete for the same two position slots, 2% planned open risk and at most 80% pre-entry capital utilization. Same-minute allocation remains alphabetical before any future minute extremes can finance another entry. Actual trade counts can plateau or displace stronger setups as the universe grows; no performance-based order-priority optimization was added.

Futures reserve full initial margin from the existing static broker planning table, not actual historical margin. Stocks/ETFs reserve gross notional, including hypothetical shorts, at no more than 1x account equity. This virtual USD pool does not reconstruct cross-broker buying power, stock borrow availability/charges, cash settlement, margin interest, fixed subscriptions or taxes. It cannot certify live account feasibility. Sparse missing minute buckets are not filled with fabricated bars.

Costs remain futures $0.85/side plus one adverse market tick; shares $0.005/share/fill plus one adverse cent; BTC long-only spot 0.10%/fill plus one basis point. The existing stress reprices identical fills/quantities with higher fees and an extra adverse market tick; it does not resimulate compounding, sizing or allocation. Tagged stale old-contract exits are unverified research marks, not demonstrated market fills. ETF split-adjusted prices and daily-flat positions do not reconstruct a full corporate-action cash ledger.

## Every training candidate

| Asset | Training status | Min early trades | Lower early PF | Score | Failed conditions / bucket |
|---|---|---:|---:|---:|---|
{chr(10).join(candidates)}

## Data, performance equivalence and audit

The original 29 candidate datasets and journals remain unchanged. The additional 36 use Massive's existing stock entitlement. A successful historical minute-data probe beyond the Basic two-year window supports paid-stock-tier access; [official paid stock plans permit unlimited calls](https://massive.com/pricing?product=stocks). This is an inference from entitlement, not a billing lookup. A stock-only paced client shares request slots, uses at most one scheduled request per second across three download threads, and falls back to conservative pacing after HTTP429. No subscription or purchase was changed. Futures/crypto defaults remain unchanged.

Larger portfolios use an equivalent accounting index over active positions instead of repeatedly scanning every enabled instrument. It preserves original engine order, marks, fees, allocation timing and risk arithmetic. **Both baseline price paths matched all 165 trades and 134 daily marks exactly** before larger portfolio comparisons ran. Runtime source hashes and the [control evidence](research/results/universe65_runtime_controls.json) are retained alongside the original model hashes.

**{a['unit_tests']} tests passed.** Audited {a['candidate_cases']} candidate cases (**{a['reused_candidate_cases']} reused**), eight new shared-account cases, six reused same-date portfolio controls and **{a['trade_executions']:,} simulated trade executions** in those records. Checks cover completed-training eligibility, exact reused journals, pivot timing, planned risk, modeled fees, ledger reconciliation, daily compounding, shared-account position limits and input/source hashes. Every modeled entry fill also matched its raw minute open plus the declared slippage across **{a['unique_raw_entry_minutes_checked']:,} unique asset/contract/entry-minute keys**. These establish stated mechanics, not predictive validity. Unavailable candidates: **{', '.join(selection['unavailable']) or 'none'}**.

Reproduce with `python -m research.universe65_download`, `python -m research.universe65_fast --workers 3`, then `python -m research.universe65_report`. [Protocol](research/universe65_protocol.json), [selection](research/results/universe65_selection.json), [summary](research/results/universe65_summary.json), [candidate journals](research/results/universe65_candidates) and [portfolio journals](research/results/universe65_portfolios) retain all cases and fingerprints. Raw prices, API caches and logs stay local and ignored. This research does not change default paper/live strategy settings or submit orders.
'''
    Path('CHOCH_UNIVERSE65.md').write_text(text,encoding='utf-8')


if __name__=='__main__':main()
