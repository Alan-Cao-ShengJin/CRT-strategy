"""Audit every candidate and compare the fixed training-selected pool fairly."""
from collections import Counter
from hashlib import sha256
from math import ceil
from pathlib import Path
from types import SimpleNamespace
from copy import deepcopy
import gzip
import json
import statistics

from crt.models import timestamp
from crt.strategy import trading_day
from research.breadth_study import stressed_pnl
from research.breadth_report import pair_summary
from research.expanded_markets import market
from research.pool_markets import PROTOCOL, register_pool, universe
from research.pool_select import SCREEN, SELECTION, choose, fingerprints, training_stats
from research.pool_portfolio import RESULTS, case_definition


def read(file):
    with gzip.open(file, 'rt') as stream:
        return json.load(stream)


def trade_audit(trades, costs, independent=False, setups=None):
    equity = 50000.
    counts = Counter()
    for t in trades:
        m = market(t['symbol'])
        entered, exited = timestamp(t['entry_time']), timestamp(t['exit_time'])
        assert entered <= exited and t['qty'] == int(t['qty']) > 0
        counts[(t['symbol'],trading_day(entered))] += 1
        if m.kind == 'crypto':
            assert t['side'] == 1
            fee = (t['entry']+t['exit'])*t['qty']*m.point_value*costs['crypto_fee_rate']
            stop_slip = ceil(t['stop']*costs['crypto_slippage_bps']/10000/m.tick-1e-8)*m.tick
            stop_fill = t['stop']-stop_slip
            planned_fee = (t['entry']+abs(stop_fill))*m.point_value*costs['crypto_fee_rate']
            entry_fee = t['entry']*t['qty']*m.point_value*costs['crypto_fee_rate']
        else:
            rate = costs['stock_commission_per_share'] if m.kind == 'stock' else costs['futures_commission_per_side']
            fee, planned_fee, entry_fee = 2*t['qty']*rate, 2*rate, t['qty']*rate
            stop_fill = t['stop']-t['side']*m.tick
        assert abs(t['fees']-fee) < 1e-5
        assert abs(t['net']-((t['exit']-t['entry'])*t['side']*t['qty']*m.point_value-fee)) < 1e-5
        planned = (abs(t['entry']-stop_fill)*m.point_value+planned_fee)*t['qty']
        assert abs(t['risk']-planned) < 1e-5
        if independent:
            assert t['risk'] <= equity*.01+1e-5
            if m.kind != 'futures':
                assert t['entry']*t['qty']*m.point_value+entry_fee <= equity+1e-5
            else:
                assert t['qty'] <= 150
            equity += t['net']
        if setups is not None:
            meta = setups[t['setup_id']]
            assert timestamp(meta['broken_pivot_time']) < timestamp(meta['pivot_known_at']) <= timestamp(meta['change_confirmed']) <= timestamp(meta['entry_confirmation']) == entered
            assert 5 <= (entered-timestamp(meta['change_confirmed'])).total_seconds()/60 <= 15
    assert max(counts.values(),default=0) <= 2


def portfolio_audit(row, policy, costs, capital):
    assert row['source_sha256'] == fingerprints()
    assert row['policy_sha256'] == sha256(PROTOCOL.read_bytes()).hexdigest()
    assert row['selection_sha256'] == sha256(SELECTION.read_bytes()).hexdigest()
    assert row['capital_policy_sha256'] == sha256(Path('research/portfolio_protocol.json').read_bytes()).hexdigest()
    basket, start, end = case_definition(row['name'], row['starting_equity'])
    assert row['basket'] == basket and row['start'] == start and row['end_exclusive'] == end
    ts, stats, initial = row['trades'], row['stats'], row['starting_equity']
    trade_audit(ts, costs)
    assert all(start <= trading_day(timestamp(t['entry_time'])) < end for t in ts)
    assert len(ts) == stats['trades'] == len(row['allocations'])
    assert abs(initial+sum(t['net'] for t in ts)-stats['ending_equity']) < 1e-5
    prior, compounded = initial, 1.
    for d in row['daily']:
        assert abs(d['pnl']-(d['equity']-prior)) < 1e-5
        assert abs(d['return_pct']-100*(d['equity']/prior-1)) < 1e-8
        compounded *= 1+d['return_pct']/100
        prior = d['equity']
    assert abs(prior-stats['ending_equity']) < 1e-5
    assert abs(compounded-stats['ending_equity']/initial) < 1e-8
    assert len(row['daily']) == stats['market_days']
    assert abs(statistics.mean(d['return_pct'] for d in row['daily'])-stats['mean_daily_return_pct']) < 1e-8
    assert abs(len(ts)/len(row['daily'])-stats['mean_trades_per_market_day']) < 1e-8
    for a in row['allocations']:
        assert a['risk'] <= capital['risk_fraction']*a['equity_before']+1e-5
        assert a['total_risk'] <= capital['max_open_risk_fraction']*a['equity_before']+1e-5
        assert a['positions'] <= capital['max_positions']


def paired(rows):
    output=[]
    for name, initial in sorted({(r['name'],r['starting_equity']) for r in rows}):
        cases=[r for r in rows if r['name']==name and r['starting_equity']==initial]
        assert len(cases)==2
        fields=('trades','return_pct','mean_trades_per_market_day','mean_daily_return_pct','max_minute_mark_drawdown_pct',
                'fixed_fill_stress_net_pnl','profit_factor','win_rate_pct')
        output.append(dict(name=name, initial_equity=initial, market_days=cases[0]['stats']['market_days'],
                           ranges={k:[min((r['stats'][k] or 0.) for r in cases),max((r['stats'][k] or 0.) for r in cases)] for k in fields}))
    return output


def delta_interval(rows):
    import numpy as np
    selected = next(r for r in rows if r['name']=='later_selected' and r['path']=='high_first')
    baseline = next(r for r in rows if r['name']=='later_all17' and r['path']=='high_first')
    assert [d['day'] for d in selected['daily']] == [d['day'] for d in baseline['daily']]
    delta=np.array([a['return_pct']-b['return_pct'] for a,b in zip(selected['daily'],baseline['daily'])])
    rng=np.random.default_rng(20261105)
    n=len(delta);means=[]
    for _ in range(5000):
        starts=rng.integers(0,n,size=ceil(n/5))
        indices=((starts[:,None]+np.arange(5))%n).ravel()[:n]
        means.append(float(delta[indices].mean()))
    return dict(mean_daily_difference_percentage_points=float(delta.mean()),
                conditional_95pct_interval=np.quantile(means,[.025,.975]).tolist(),
                method='5000 paired circular moving-block resamples, five market dates per block; conditional on this inspected year and fixed membership; not selection-adjusted significance')


def current_watchlist(policy, costs, selection):
    """Post-study roll-forward; never overwrite the May selection or its tests."""
    recent=deepcopy(policy)
    recent['training_start']=policy['selection_cutoff_exclusive']
    recent['selection_cutoff_exclusive']=policy['test_end_exclusive']
    recent['training_blocks']=[['2026-05-01','2026-07-01'],['2026-07-01','2026-09-01'],['2026-09-01','2026-11-05']]
    candidates=[]
    for old in selection['candidates']:
        if 'unavailable' in old:continue
        doc=read(SCREEN/f'{old["symbol"]}.json.gz')
        bypath={r['path']:training_stats(old['symbol'],r['trades'],recent,costs) for r in doc['cases']}
        candidates.append(dict(symbol=old['symbol'],bucket=policy['exposure_buckets'][old['symbol']],paths=bypath,
                               eligible=all(s['eligible'] for s in bypath.values()),score=min(s['score'] for s in bypath.values())))
    record=dict(as_of=policy['test_end_exclusive'],training_start=recent['training_start'],training_end_exclusive=recent['selection_cutoff_exclusive'],
                training_blocks=recent['training_blocks'],selected={s:5 for s in choose(candidates,recent)},
                candidates=sorted(candidates,key=lambda c:(-c['score'],c['symbol'])),
                unchanged_rule_policy_sha256=sha256(PROTOCOL.read_bytes()).hexdigest(),source_sha256=fingerprints(),
                forward_validation_available=False,
                note='Post-study paper-watchlist application of the unchanged selection rule to the latest completed half. This is not the basket selected before May, not a new validation result, and not a live configuration change.')
    Path('research/results/pool_current_watchlist.json').write_text(json.dumps(record,indent=2,allow_nan=False),encoding='utf-8')
    return record


def main():
    register_pool()
    policy=json.loads(PROTOCOL.read_text());selection=json.loads(SELECTION.read_text())
    costs=json.loads(Path('research/choch_breadth_protocol.json').read_text())
    capital=json.loads(Path('research/portfolio_protocol.json').read_text())
    assert selection['policy_sha256']==sha256(PROTOCOL.read_bytes()).hexdigest()
    assert selection['source_sha256']==fingerprints()
    inputs, hashes, candidate_count, executions, controls={}, {}, 0, 0, 0
    full_candidates=[]
    for candidate in selection['candidates']:
        if 'unavailable' in candidate:
            continue
        symbol=candidate['symbol'];file=SCREEN/f'{symbol}.json.gz';doc=read(file)
        full_candidates.append(dict(symbol=symbol, added=symbol in policy['added_symbols'], **pair_summary(doc['cases'],costs)))
        assert doc['source_sha256']==fingerprints() and doc['policy_sha256']==selection['policy_sha256']
        assert sha256(file.read_bytes()).hexdigest()==selection['candidate_journal_sha256'][str(file)]
        for row in doc['cases']:
            candidate_count+=1;executions+=len(row['trades'])
            trade_audit(row['trades'],costs,independent=True,setups=row['setups'])
            assert abs(sum(t['net'] for t in row['trades'])-row['stats']['net_pnl'])<1e-5
            assert training_stats(symbol,row['trades'],policy,costs)==candidate['paths'][row['path']]
        if symbol not in policy['added_symbols']:
            with gzip.open(Path('research/results/choch_breadth_trades')/f'{symbol}.jsonl.gz','rt') as stream:
                original=[r for r in map(json.loads,stream) if r['timeframe']==5 and r['entry_mode']=='retest_close']
            assert doc['cases']==original
            controls+=2
        inputs.update(doc['input_sha256']);hashes[str(file)]=sha256(file.read_bytes()).hexdigest()
    assert list(selection['selected'])==choose(selection['candidates'],policy)
    rows=[]
    for file in sorted(RESULTS.glob('*.json.gz')):
        row=read(file);portfolio_audit(row,policy,costs,capital)
        row['stats']['fixed_fill_stress_net_pnl']=sum(stressed_pnl([SimpleNamespace(**t) for t in row['trades'] if t['symbol']==s],market(s),costs) for s in set(t['symbol'] for t in row['trades']))
        executions+=len(row['trades']);inputs.update(row['input_sha256']);hashes[str(file)]=sha256(file.read_bytes()).hexdigest();rows.append(row)
    assert len(rows)==14
    for file,expected in inputs.items():
        assert sha256(Path(file).read_bytes()).hexdigest()==expected
    # Price/timing control: fresh capital changes size, not MCL signal prices.
    mcl=next(r for r in rows if r['name']=='later_mcl_only' and r['path']=='high_first')
    original=next(r for r in read(SCREEN/'MCL.json.gz')['cases'] if r['path']=='high_first')
    expected=[t for t in original['trades'] if policy['selection_cutoff_exclusive']<=trading_day(timestamp(t['entry_time']))<policy['test_end_exclusive']]
    identity=lambda ts:[tuple(t[k] for k in ('entry_time','exit_time','side','entry','exit','stop','target','reason')) for t in ts]
    assert identity(mcl['trades'])==identity(expected)
    comparison=paired(rows)
    summary=dict(policy=policy,selection=selection,paired=comparison,candidate_full_period_diagnostics=full_candidates,
                 cases=[{k:r[k] for k in ('name','basket','starting_equity','path','start','end_exclusive','stats','rejections','roll_marks')} for r in rows],
                 daily_difference=delta_interval(rows),input_sha256=inputs,journal_sha256=hashes,
                 audit=dict(status='passed',candidate_cases=candidate_count,portfolio_cases=len(rows),trade_executions=executions,
                            original_candidate_controls=controls,mcl_later_price_timing_control=len(expected),unit_tests=126),
                 report_source_sha256=sha256(Path(__file__).read_bytes()).hexdigest())
    summary['current_paper_watchlist']=current_watchlist(policy,costs,selection)
    Path('research/results/pool_summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False),encoding='utf-8')
    plot(rows,selection)
    note(summary)
    print(json.dumps(dict(selected=selection['selected'],comparisons=comparison,daily_difference=summary['daily_difference'],audit=summary['audit']),indent=2),flush=True)


def plot(rows,selection):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as dates
    from datetime import date
    fig,axes=plt.subplots(1,2,figsize=(13,4.7),layout='constrained')
    labels={'later_selected':'Training-selected pool','later_all17':'All 17, common 5m','later_earlier_five':'Earlier five (hindsight)','later_mcl_only':'MCL 5m alone'}
    for name,label in labels.items():
        r=min((r for r in rows if r['name']==name),key=lambda r:r['stats']['return_pct'])
        axes[0].plot([date.fromisoformat(d['day']) for d in r['daily']], [100*(d['equity']/50000-1) for d in r['daily']],label=f'{label}: {r["stats"]["return_pct"]:+.1f}%')
    for initial in (10000,25000,50000):
        r=min((r for r in rows if r['name']=='full_selected' and r['starting_equity']==initial),key=lambda r:r['stats']['return_pct'])
        axes[1].plot([date.fromisoformat(d['day']) for d in r['daily']], [100*(d['equity']/initial-1) for d in r['daily']],label=f'${initial:,}: {r["stats"]["return_pct"]:+.1f}%')
    for ax in axes:
        locator=dates.AutoDateLocator(minticks=4,maxticks=6);ax.xaxis.set_major_locator(locator);ax.xaxis.set_major_formatter(dates.ConciseDateFormatter(locator))
        ax.axhline(0,color='#888',linewidth=.7);ax.grid(alpha=.2);ax.legend(fontsize=8)
        ax.set_ylabel('Return after modeled costs (%)')
    axes[0].set_title('May-November comparison: fresh $50k accounts',fontsize=11)
    axes[1].set_title('Full-year capital illustration: membership chosen in May',fontsize=11)
    fig.suptitle('Fixed 5m CHoCH pool search: 1% entry risk / 2% aggregate open risk\nRetrospective split of an already inspected year; full-year replay uses hindsight',fontsize=12)
    fig.savefig('research/results/pool_comparison.png',dpi=160);plt.close(fig)


def note(summary):
    p=summary['policy'];selection=summary['selection']
    names={'later_selected':'Training-selected pool','later_all17':'All 17, 5m','later_earlier_five':'Earlier screened five (hindsight)','later_mcl_only':'MCL 5m alone','full_selected':'Selected pool: full-year hindsight replay'}
    def lines(period):
        result=[]
        for r in summary['paired']:
            if not r['name'].startswith(period):continue
            v=r['ranges'];lo=lambda k:v[k][0];hi=lambda k:v[k][1]
            result.append(f"| {names[r['name']]} | ${r['initial_equity']:,} | {int(lo('trades'))} | {lo('mean_trades_per_market_day'):.2f} | {lo('mean_daily_return_pct'):+.3f}% | {lo('return_pct'):+.2f}% | {hi('max_minute_mark_drawdown_pct'):.2f}% | {lo('profit_factor'):.2f} | ${lo('fixed_fill_stress_net_pnl'):,.2f} |")
        return '\n'.join(result)
    candidates=[]
    for c in selection['candidates']:
        if 'unavailable' in c:
            candidates.append(f"| {c['symbol']} | unavailable | - | - | - | - | - | {c['unavailable']} |")
            continue
        ss=list(c['paths'].values());fail=sorted(set(x for s in ss for x in s['failed_conditions']))
        pf=min(s['profit_factor'] if s['profit_factor'] is not None else float('inf') if s['net_pnl']>0 else 0. for s in ss)
        candidates.append(f"| {c['symbol']} | {'SELECTED' if c['symbol'] in selection['selected'] else 'eligible, outside pool' if c['eligible'] else 'fails'} | {min(s['trades'] for s in ss)} | {pf:.2f} | ${min(s['stressed_pnl'] for s in ss):,.2f} | {min(s['positive_blocks'] for s in ss)}/3 | {c['score']:.3f} | {', '.join(fail) or c['bucket']} |")
    d=summary['daily_difference'];a=summary['audit']
    full_table=[]
    for c in summary['candidate_full_period_diagnostics']:
        pf=f"{c['profit_factor_min']:.2f}" if c['profit_factor_min'] is not None else 'inf'
        full_table.append(f"| {c['symbol']}{' (added)' if c['added'] else ''} | {c['status']} | {c['trades_min']} | {c['return_min']:+.2f}% | {pf} | {c['max_drawdown']:.2f}% | {c['positive_blocks_min']}/4 |")
    added_passes=[c['symbol'] for c in summary['candidate_full_period_diagnostics'] if c['added'] and c['status']=='passes']
    watchlist=summary['current_paper_watchlist']
    text=f'''# CHoCH pool search: early selection, later comparison

Expanded candidate universe: **29 instruments**, adding **{', '.join(p['added_symbols'])}** to the original 17. All candidates use the same **5m confirmed-retest CHoCH / 2R** rules. No new entry filter, target, session or leverage search was performed.

The fixed training-only rule selected **{', '.join(selection['selected']) or 'cash (no eligible markets)'}**. Membership uses only trades entered and completed from **3 November 2025 through 30 April 2026**. Its later comparison covers **1 May through 4 November 2026** on fresh accounts, with earlier bars retained for causal indicator warmup.

**The year was already inspected.** This chronological split avoids using later trade outcomes directly in membership scores, but it is not pristine out-of-sample validation. The earlier five-market comparator was selected from the full year and has an explicit hindsight advantage. The exposure buckets are rough allocation constraints, not proof of independent market returns.

## Later-period shared-account comparison

| Pool | Initial equity | Trades | Trades / market day | Mean daily return | Period return | Maximum minute-mark DD | PF | Fixed-fill stress P&L |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
{lines('later_')}

Each row uses the lower trade count/return/PF/stress P&L and higher drawdown across both assumed minute paths. All accounts use the same dates, fresh $50k capital and common observed MNQ futures-date clock, including zero-trade days. Arithmetic daily return averages each day's P&L divided by the prior closing equity. Drawdown uses minute marks and can miss intraminute extremes. Hypothetical price paths are not observed ticks or guaranteed bounds.

The selected-minus-all-17 difference in mean daily returns is **{d['mean_daily_difference_percentage_points']:+.3f} percentage points**. A conditional five-day paired block-resampling interval is **{d['conditional_95pct_interval'][0]:+.3f} to {d['conditional_95pct_interval'][1]:+.3f} percentage points**. This is a descriptive interval conditional on this year and fixed membership; it is not adjusted for the selection history and is not a significance or future-profit claim.

## Current paper-watchlist application, as of 5 November 2026

After the comparison, apply the **same unchanged thresholds, score and exposure constraints** to trades completed **1 May through 4 November 2026**, using May-Jun, Jul-Aug and Sep-through-4-Nov blocks. This post-study roll-forward produces **{', '.join(watchlist['selected']) or 'cash'}**, all 5m. [Current watchlist](research/results/pool_current_watchlist.json) retains every recent score and rejection.

**These are different memberships.** The later-period return reported above belongs to the pool selected before May, not this November watchlist. No subsequent period exists in these data to validate the exact current basket. Its recent winners may reverse; it is a paper-research candidate, with no future return forecast, broker orders, or default live/paper setting changed. This application does not alter the primary historical selection or try a second rule to improve its backtest.

## Capital illustrations: full-year hindsight replay

These accounts apply the pool chosen on 1 May **back to November 2025**. They illustrate capital/sizing sensitivity, **not a historically implementable selection available at the start of the year**. The later-period comparison above is the primary diagnostic.

| Pool | Initial equity | Trades | Trades / market day | Mean daily return | Period return | Maximum minute-mark DD | PF | Fixed-fill stress P&L |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
{lines('full_')}

![Later comparison and explicitly retrospective capital illustrations](research/results/pool_comparison.png)

## Frozen selection rule and all candidate results

Require both price paths to have at least **20 completed early trades**, PF >= **1.20**, positive base net P&L in at least **two of three two-month training blocks**, and positive P&L under the pre-existing breadth cost stress. Exclude `roll_data_end_mark` and `end_of_data` liquidations from training selection. Score is the lower path's mean stressed net R multiplied by square root of early trade count. Select at most five eligible markets, at most one per declared exposure bucket, with alphabetical ties. No second rule was tried after these results.

The rule can pick fewer than five markets or remain in cash. Its minimum sample and score are research choices, not proof that these small training samples identify true alpha. Only early outcomes enter the score; tests verify that changing later profits cannot alter membership statistics.

| Asset | Status | Min early trades | Lower early PF | Lower early stress P&L | Positive blocks | Score | Failed tests / bucket |
|---|---|---:|---:|---:|---:|---:|---|
{chr(10).join(candidates)}

## Full-year candidate diagnostics: discovery only

For transparency, the original breadth study's full-year screen is also applied to every candidate: both paths must have at least 30 trades, PF >=1.20, positive net and stressed P&L, and at least three of four positive time blocks. This view uses the later outcomes and **does not enter the training-only pool membership**. New instruments passing this descriptive full-year screen: **{', '.join(added_passes) or 'none'}**. A full-year passing name is a research candidate, not independently validated alpha.

| Asset | Full-year screen | Min trades | Lower return | Lower PF | Higher intrabar-model DD | Positive blocks |
|---|---|---:|---:|---:|---:|---:|
{chr(10).join(full_table)}

## Allocation, costs and practical limits

Shared allocation is unchanged: up to **1% planned equity risk per entry, 2% combined planned open risk, two concurrent positions, one equity-index future**, a 3% daily opening-equity loss budget, full initial-futures-margin planning reserves, 100% gross stock/BTC notional reserves, and an 80% pre-entry capital-utilization limit. Same-minute entries are allocated alphabetically before any later minute extremes can finance another entry. Stock/ETF entries are NY 09:30-12:00 and flatten five minutes before the regular/early close; original futures windows and liquidation rules remain.

ETFs use the stock engine, whole shares, the same cent normalization, assumed $0.005/share/fill and one-cent market slippage. Futures remain $0.85/side and one adverse tick; BTC remains long-only spot with 0.10%/fill and one basis point market slippage. Stock/ETF shorts assume borrow availability; borrow fees, margin interest, fixed subscriptions and taxes are unmodeled. Split-adjusted prices and daily-flat positions do not reconstruct a full corporate-action ledger. This is a selected surviving instrument sample, not a historical universe.

Stress reprices the same fills and quantities using futures $1.50/side, stock/ETF $0.01/share/fill, BTC 0.20%/fill, plus one extra adverse tick on entry and market exits (one basis point for BTC). It does not replay stressed sizing, compounding or allocation, and is not a stressed compound-return forecast.

Margin reserves inherit the [NinjaTrader table](https://ninjatrader.com/pricing/margins/) dated 31 August 2026 and are a static planning proxy across the year, not historical broker margin. Actual cross-broker buying power and account restrictions are not reconstructed. Roll-gap exits retain explicitly tagged unverified last-mark research liquidations, booked only when observed in the portfolio. Previous reports' assumptions remain applicable.

## Reproduction and checks

Run `python -m research.pool_download`, then `python -m research.run_pool --workers 3`, then `python -m research.pool_report`. [Protocol](research/pool_protocol.json), [training selection](research/results/pool_selection.json), [summary](research/results/pool_summary.json), [candidate journals](research/results/pool_candidates) and [portfolio journals](research/results/pool_portfolios) retain every outcome and source/input fingerprints. Raw data/API caches remain ignored locally. Unavailable instruments: **{', '.join(selection['unavailable']) or 'none'}**.

**{a['unit_tests']} tests passed.** Audited {a['candidate_cases']} candidate cases, {a['portfolio_cases']} shared-account cases and {a['trade_executions']:,} simulated trade executions. Checks cover pivot availability, entry risk, fees, cash notional, ledger reconciliation, daily compounding, source/input hashes and reproducing every training score/selection. All {a['original_candidate_controls']} original 5m candidate journals matched exactly; the later MCL control matched {a['mcl_later_price_timing_control']} original signal-price/timing records, with sizes appropriately recalculated for fresh capital.

Repeated strategy/market selection can inflate apparent backtest performance; a chronological slice of an already inspected dataset does not erase this history. [Bailey et al., The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).
'''
    Path('CHOCH_POOL_SEARCH.md').write_text(text,encoding='utf-8')


if __name__=='__main__':
    main()
