"""Audit fixed frequency variants and assess daily-return aspirations honestly."""
from collections import Counter
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from math import ceil
import gzip
import json
import statistics

from crt.config import Config
from crt.models import timestamp
from crt.strategy import session_at,trading_day
from research.breadth_study import stressed_pnl
from research.expanded_markets import market
from research.frequency_study import PROTOCOL,RESULTS,model_hashes
from research.pool_markets import register_pool
from research.pool_select import SELECTION


def read(path):
    with gzip.open(path,'rt') as stream:return json.load(stream)


def audit(r,policy,capital,costs):
    assert r['frequency_source_sha256']==model_hashes()
    assert r['frequency_policy_sha256']==sha256(PROTOCOL.read_bytes()).hexdigest()
    assert r['selection_sha256']==sha256(SELECTION.read_bytes()).hexdigest()
    assert r['basket']==json.loads(SELECTION.read_text())['selected']
    s=r['stats'];ts=r['trades'];variant=policy['variants'][r['variant']]
    assert len(ts)==s['trades']==len(r['allocations'])
    assert abs(sum(t['net'] for t in ts)-(s['ending_equity']-policy['initial_equity']))<1e-5
    configs={symbol:Config(**c).validate() for symbol,c in r['configs'].items()}
    count=Counter()
    for t in ts:
        m=market(t['symbol']);cfg=configs[t['symbol']];at=timestamp(t['entry_time'])
        assert policy['start']<=trading_day(at)<policy['end_exclusive']
        assert timestamp(t['exit_time'])>=at and session_at(at,cfg) is not None
        assert t['qty']==int(t['qty'])>0
        count[(t['symbol'],trading_day(at))]+=1
        fee=(2*cfg.commission_per_side[t['symbol']]*t['qty'] if m.kind!='crypto'
             else (t['entry']+t['exit'])*m.point_value*t['qty']*costs['crypto_fee_rate'])
        assert abs(t['fees']-fee)<1e-5
        assert abs(t['net']-((t['exit']-t['entry'])*t['side']*t['qty']*m.point_value-fee))<1e-5
        distance=t['side']*(t['entry']-t['stop']);reward=t['side']*(t['target']-t['entry'])
        assert reward+1e-7>=2*distance and reward-2*distance<m.tick+1e-7
    assert max(count.values(),default=0)<=(6 if variant['repeat_setups'] else 2)
    for a in r['allocations']:
        assert a['risk']<=capital['risk_fraction']*a['equity_before']+1e-5
        assert a['total_risk']<=capital['max_open_risk_fraction']*a['equity_before']+1e-5
        assert a['positions']<=capital['max_positions']
    prior=policy['initial_equity'];factor=1.
    for d in r['daily']:
        assert abs(d['pnl']-(d['equity']-prior))<1e-5
        assert abs(d['return_pct']-100*(d['equity']/prior-1))<1e-8
        factor*=1+d['return_pct']/100;prior=d['equity']
    assert abs(factor-s['ending_equity']/policy['initial_equity'])<1e-8
    assert abs(statistics.mean(d['return_pct'] for d in r['daily'])-s['mean_daily_return_pct'])<1e-8
    assert len(r['daily'])==s['market_days']
    assert abs(len(ts)/s['market_days']-s['mean_trades_per_market_day'])<1e-8


def main():
    register_pool();policy=json.loads(PROTOCOL.read_text())
    capital=json.loads(Path('research/portfolio_protocol.json').read_text())
    costs=json.loads(Path('research/choch_breadth_protocol.json').read_text())
    rows=[];inputs={};hashes={}
    for variant in policy['variants']:
        for path in policy['paths']:
            file=RESULTS/f'{variant}_{path}.json.gz';r=read(file);audit(r,policy,capital,costs)
            if variant=='baseline':
                old=read(Path('research/results/pool_portfolios')/f'later_selected_50000_{path}.json.gz')
                assert r['trades']==old['trades'] and r['daily']==old['daily']
            r['stats']['fixed_fill_stress_net_pnl']=sum(stressed_pnl([SimpleNamespace(**t) for t in r['trades'] if t['symbol']==s],market(s),costs) for s in set(t['symbol'] for t in r['trades']))
            rows.append(r);inputs.update(r['input_sha256']);hashes[str(file)]=sha256(file.read_bytes()).hexdigest()
    for file,digest in inputs.items():assert sha256(Path(file).read_bytes()).hexdigest()==digest
    paired=[]
    fields=('trades','mean_trades_per_market_day','mean_daily_return_pct','return_pct','max_minute_mark_drawdown_pct','profit_factor','fixed_fill_stress_net_pnl')
    for variant in policy['variants']:
        cases=[r for r in rows if r['variant']==variant]
        paired.append(dict(variant=variant,ranges={k:[min(r['stats'][k] or 0 for r in cases),max(r['stats'][k] or 0 for r in cases)] for k in fields}))
    baseline=next(r for r in rows if r['variant']=='baseline' and r['path']=='high_first')
    index={(t['symbol'],t['entry_time']):t for t in baseline['trades']}
    mean_trade=statistics.mean(100*index[(a['symbol'],a['time'])]['net']/a['equity_before'] for a in baseline['allocations'])
    target_math={str(target):ceil(target/mean_trade) for target in (1,2,5,9)}
    record=dict(policy=policy,basket=baseline['basket'],paired=paired,
                inherited_policy_sha256={str(p):sha256(p.read_bytes()).hexdigest() for p in
                    (Path('research/portfolio_protocol.json'),Path('research/choch_breadth_protocol.json'),SELECTION)},
                cases=[{k:r[k] for k in ('variant','path','stats','rejections','roll_marks')} for r in rows],
                illustrative_mean_trade_account_return_pct=mean_trade,illustrative_trades_for_daily_target_if_unchanged=target_math,
                reached_1pct_mean_daily_both_paths=[p['variant'] for p in paired if p['ranges']['mean_daily_return_pct'][0]>=1],
                input_sha256=inputs,journal_sha256=hashes,audit=dict(status='passed',cases=8,
                trade_executions=sum(len(r['trades']) for r in rows),baseline_trade_for_trade_controls=2,unit_tests=136),
                report_source_sha256=sha256(Path(__file__).read_bytes()).hexdigest())
    Path('research/results/frequency_summary.json').write_text(json.dumps(record,indent=2,allow_nan=False),encoding='utf-8')
    plot(record);note(record)
    print(json.dumps(dict(comparisons=paired,reached_1pct=record['reached_1pct_mean_daily_both_paths'],audit=record['audit']),indent=2),flush=True)


def plot(record):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    labels=['Original','Wider hours','Repeat setups','Both changes']
    fig,axes=plt.subplots(1,2,figsize=(11,4.5),layout='constrained')
    for ax,key,title in zip(axes,['mean_trades_per_market_day','mean_daily_return_pct'],['Trades per market day','Arithmetic mean daily return (%)']):
        values=[p['ranges'][key][0] for p in record['paired']]
        bars=ax.bar(labels,values,color=['#526e9f','#398d80','#a18b48','#9568a3'])
        ax.bar_label(bars,fmt='%.3f' if 'return' in key else '%.2f',padding=4)
        ax.set_title(title);ax.axhline(0,color='#555',linewidth=.7);ax.tick_params(axis='x',labelrotation=15)
        ax.margins(y=.2);ax.grid(axis='y',alpha=.15)
    axes[1].axhline(1,color='#c14b47',linestyle='--',label='1% daily aspiration')
    axes[1].legend(fontsize=8)
    fig.suptitle('Fixed 5m pool: more opportunities, unchanged 1% entry / 2% combined open risk\nMay-November 2026, modeled costs, lower result across hypothetical price paths',fontsize=11)
    fig.savefig('research/results/frequency_comparison.png',dpi=160);plt.close(fig)


def note(r):
    labels={'baseline':'Original','wider_hours':'Wider hours','repeat_setups':'Repeated distinct setups','wider_and_repeat':'Both changes'}
    lines=[]
    for p in r['paired']:
        v=p['ranges']
        lines.append(f"| {labels[p['variant']]} | {int(v['trades'][0])} | {v['mean_trades_per_market_day'][0]:.2f} | {v['mean_daily_return_pct'][0]:+.3f}% | {v['return_pct'][0]:+.2f}% | {v['max_minute_mark_drawdown_pct'][1]:.2f}% | {v['profit_factor'][0]:.2f} | ${v['fixed_fill_stress_net_pnl'][0]:,.2f} |")
    reached=', '.join(r['reached_1pct_mean_daily_both_paths']) or 'none'
    text=f'''# More daily trades: controlled CHoCH frequency test

Fixed basket: **{', '.join(r['basket'])}**, selected before May using the earlier training period. Test **1 May-4 November 2026**, fresh **$50,000**, 5m confirmed-retest entries and 2R targets. This is a retrospective extension of inspected dates, not new untouched validation.

## All four declared variants

| Variant | Trades | Trades / market day | Mean daily return | Period return | Maximum minute-mark DD | PF | Fixed-fill stress P&L |
|---|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(lines)}

Variants reaching at least 1% arithmetic mean daily return under both assumed paths: **{reached}**. Daily denominators include zero-trade market dates. Rows report lower trades/returns/PF/stress and higher drawdown across paths, not confidence bounds. Minute-mark drawdown can miss intraminute extremes.

![Frequency versus return, with unchanged risk](research/results/frequency_comparison.png)

## What changed, and what the comparison means

Wider hours extend stock/ETF entries to NY 09:30-15:30, futures to NY 09:30-15:30 and 18:30-23:30. Original liquidation calendars and five-minutes-before-close exits remain. Repeated-setups mode releases the one-signal-per-session reservation and raises each asset's daily cap from two to six, while still requiring a new, unconsumed CHoCH pivot and valid retest. A held position still prevents another entry in that asset. No recycling losing setups or adding to positions.

Risk is unchanged: up to **1% planned account risk per entry, 2% aggregate open risk, two concurrent positions, one equity-index future, 3% daily opening-equity loss budget**, per-asset two-consecutive-loss halt, full initial-futures-margin planning reserves and gross stock notional reserves. Gaps can exceed planned risk. All four cases and both paths were frozen before their results; no additional rule search was used to force a daily target.

These tests alter opportunity frequency, not the signal timeframe, structural stop, target, market membership or position-risk percentage. More fills are useful only if their net expectancy and capital efficiency are positive after execution costs. Previous 1m-3m breadth tests already increased trade counts while most asset cells lost money; faster candles are not evidence of a stronger edge.

## Daily-target arithmetic, not a forecast

The original basket's mean net trade return was **{r['illustrative_mean_trade_account_return_pct']:.4f}% of its entry account equity**. If that exact edge and sizing could be retained on every extra opportunity, rough arithmetic would require **{r['illustrative_trades_for_daily_target_if_unchanged']['1']} trades/day for 1%**, **{r['illustrative_trades_for_daily_target_if_unchanged']['2']} for 2%**, **{r['illustrative_trades_for_daily_target_if_unchanged']['5']} for 5%**, or **{r['illustrative_trades_for_daily_target_if_unchanged']['9']} for 9%**. This linear illustration ignores changing equity within the day, correlations, risk halts and selection degradation. It is not a projection, an achievable setup count or an execution plan.

A constant compounded 1% every market day would multiply capital about **12.3 times in 252 days**. A variable arithmetic mean of 1% does not guarantee that compound growth. The user's 1%-9% aspiration is not supported merely by increasing turnover, and this experiment does not promise such returns or change risk to manufacture them.

## Costs and practical limits

Base costs remain futures $0.85/side and one adverse market tick; stock/ETF $0.005/share/fill and one adverse cent. Stress reprices the same fills and quantities with the pre-existing higher commissions and extra adverse tick; it does not resimulate sizing or compound returns under stressed costs. Borrow fees, margin interest, fixed subscriptions, actual margin schedules and broker buying power are not reconstructed. A gross-notional allocation reserve is **not a settled-cash ledger**: repeated stock trading needs the appropriate account/settlement arrangements. [FINRA's frequent intraday trading guidance](https://syndication.finra.org/content/frequent-intraday-trading-understanding-basics).

The account is one virtual USD pool; actual cross-broker transfers and account restrictions can reduce feasible frequency. These are hypothetical OHLC execution paths, not tick fills. Tagged stale-mark research liquidations remain unverified executable exits. The results do not certify live trading feasibility.

## Reproduction and audit

Run `python -m research.frequency_study --workers 3`, then `python -m research.frequency_report`. [Protocol](research/frequency_protocol.json), [summary](research/results/frequency_summary.json) and [all eight journals](research/results/frequency) retain configurations, trades, daily equity, allocations and source/input hashes. Raw prices remain local and ignored. No default strategy or broker orders changed.

**136 tests passed**, including unchanged-risk checks and ensuring session repetition never clears consumed-pivot protection. {r['audit']['trade_executions']:,} simulated executions passed fee/ledger, timing, entry-risk, daily-return and input/source checks. Both baseline paths matched the previous pool's full trade lists and daily marks exactly. These checks substantiate simulation mechanics, not predictive profitability.
'''
    Path('CHOCH_FREQUENCY.md').write_text(text,encoding='utf-8')


if __name__=='__main__':main()
