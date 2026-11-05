"""Plot saved daily marks; never reruns or selects strategy parameters."""
from pathlib import Path
import json

import matplotlib
matplotlib.use('Agg')
import matplotlib.dates as mdates
import matplotlib.pyplot as plt


def main():
    doc = json.loads(Path('research/results/choch_summary.json').read_text())
    rules, rows = doc['protocol'], doc['results']
    figure, axes = plt.subplots(3, 1, figsize=(12, 10), sharex=True, layout='constrained')
    for symbol, axis in zip(rules['symbols'], axes):
        baseline = [r for r in rows if r['symbol'] == symbol and r['strategy'] == 'crt_rebreak']
        days = sorted(d for d in baseline[0]['daily_last_marks']
                      if rules['start_trading_date'] <= d < rules['end_exclusive_trading_date'])
        dates = [mdates.datestr2num(d) for d in days]
        curves = [[100*(r['daily_last_marks'][d]['equity']/rules['initial_equity']-1) for d in days] for r in baseline]
        axis.fill_between(dates, [min(v) for v in zip(*curves)], [max(v) for v in zip(*curves)],
                          color='#60758a', alpha=.3, label='CRT rebreak: two assumed paths')
        for mode, color, label in [('next_open', '#2674b8', 'CHoCH: next-open entry'),
                                    ('retest_close', '#16845e', 'CHoCH: confirmed retest')]:
            row = next(r for r in rows if r['symbol'] == symbol and r['strategy'] == 'choch'
                       and r['timeframe'] == rules['primary_timeframe']
                       and r['nominal_reward_risk'] == rules['primary_reward_risk']
                       and r['entry_mode'] == mode and r['path'] == 'high_first')
            values = [100*(row['daily_last_marks'][d]['equity']/rules['initial_equity']-1) for d in days]
            axis.plot(dates, values, color=color, linewidth=1.7, label=label)
        axis.axhline(0, color='#67717d', linewidth=.8)
        axis.set_title(symbol, loc='left', fontweight='bold')
        axis.set_ylabel('Account return (%)')
        axis.grid(alpha=.17)
    axes[0].legend(loc='upper left', fontsize=9)
    axes[-1].xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
    figure.suptitle('CHoCH primary specification versus CRT rebreak\n'
                    '5-minute CHoCH, 2R target | independent $50k accounts, up to 1% risk, assumed costs', fontsize=12)
    figure.savefig('research/results/choch_comparison.png', dpi=160)
    plt.close(figure)


if __name__ == '__main__':
    main()
