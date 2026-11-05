# MNQ filter research and direct-break comparison

This is retrospective research on 3 November 2025–4 November 2026. **The whole year was already inspected in prior work. The later split is not pristine out-of-sample or prospective evidence.** It is used to check whether earlier-period filter selection survives a different chronological period.

Baseline: mechanical CRT, fixed 1.5 nominal reward/risk, $50,000 initial account, 1% current-equity planned risk, same assumed commissions/slippage and whole-contract sizing as [the risk-sizing study](RISK_SIZING.md). Direct-break enters C3's first break of the relevant C2 extreme; rebreak additionally requires an observed (within the hypothetical path) break, pullback and second break.

**Result: 0 of 4 shortlisted filters improved the later-period win rate versus the corresponding baseline under both bar paths.** A modest full-year improvement for New York-only direct entries did not improve the later-period comparison. These tests do not validate a new win-rate filter for deployment.

Ten simple single filters were declared in [filter_protocol.json](research/filter_protocol.json). They concern session/time, 15-minute setup timeframe, C2 body direction, rejection strength, sweep depth, preceding price efficiency, minimum target distance, the existing hourly bias proxy, and Mondays. No filter combinations or threshold grid were searched. All filter features are available when C2 closes; no C3 high/low or future trade result is used to accept a setup.

Only trades entered before **1 July 2026** are used to shortlist two filters per entry method. Selection ranks the minimum Wilson win-rate lower endpoint across high-first/low-first assumptions, requiring at least 40 earlier-period baseline trades under each path. This first stage is subset screening, not a deployable policy backtest. The shortlisted filters are then **fully resimulated**: rejected setups may be replaced by the next qualified setup. The later results do not change which filters are selected.

## Full policy resimulations

Ranges compare the two assumed minute OHLC paths; they are not confidence bounds or rigorous best/worst bounds. Net-positive win rate includes modeled fees and slippage. Period return uses that period's starting equity; the later period continues the earlier account rather than starting a fresh one.

| Entry | Filter | Period | Trades | Net win rate | Mean net R | Compounded return |
|---|---|---|---:|---:|---:|---:|
| direct | baseline | Earlier: Nov–Jun | 220 | 45.0% to 49.5% | -0.05 to 0.05 | -9.45 to 10.60% |
| direct | baseline | Later: Jul–Nov | 127 | 48.0% to 54.3% | 0.03 to 0.17 | 2.80 to 23.90% |
| direct | baseline | Full year | 347 | 46.1% to 51.3% | -0.02 to 0.10 | -6.91 to 37.03% |
| direct | ny_only | Earlier: Nov–Jun | 124 | 50.0% to 54.0% | 0.05 to 0.15 | 6.52 to 18.76% |
| direct | ny_only | Later: Jul–Nov | 69 | 46.4% to 50.7% | 0.02 to 0.12 | 2.11 to 8.21% |
| direct | ny_only | Full year | 193 | 48.7% to 52.8% | 0.04 to 0.14 | 8.77 to 28.51% |
| direct | strong_rejection | Earlier: Nov–Jun | 244 | 42.6% to 45.1% | -0.07 to -0.02 | -17.42 to -5.89% |
| direct | strong_rejection | Later: Jul–Nov | 124 to 125 | 42.7% to 48.0% | -0.06 to 0.05 | -7.40 to 6.15% |
| direct | strong_rejection | Full year | 368 to 369 | 42.7% to 46.1% | -0.07 to 0.01 | -23.53 to -0.11% |
| rebreak | baseline | Earlier: Nov–Jun | 131 to 133 | 42.1% to 43.5% | -0.05 to -0.04 | -7.62 to -4.49% |
| rebreak | baseline | Later: Jul–Nov | 79 to 80 | 57.0% to 58.8% | 0.28 to 0.32 | 22.75 to 26.84% |
| rebreak | baseline | Full year | 211 to 212 | 47.6% to 49.3% | 0.07 to 0.10 | 13.39 to 21.15% |
| rebreak | directional_c2_body | Earlier: Nov–Jun | 150 to 153 | 43.1% to 43.3% | -0.01 to -0.00 | -3.11 to -2.37% |
| rebreak | directional_c2_body | Later: Jul–Nov | 87 to 88 | 46.0% to 46.6% | 0.03 to 0.05 | 3.69 to 5.05% |
| rebreak | directional_c2_body | Full year | 237 to 241 | 44.3% to 44.4% | 0.01 to 0.02 | 0.46 to 2.56% |
| rebreak | ny_only | Earlier: Nov–Jun | 67 to 68 | 46.3% to 47.1% | 0.04 to 0.06 | 2.22 to 3.44% |
| rebreak | ny_only | Later: Jul–Nov | 42 to 43 | 51.2% to 52.4% | 0.15 to 0.18 | 8.05 to 8.94% |
| rebreak | ny_only | Full year | 110 | 48.2% to 49.1% | 0.08 to 0.10 | 10.45 to 12.68% |

Win rate is one criterion. A filter can raise it while reducing trade count, expectancy, or return, and an apparent improvement on a small later sample is weak evidence. A Wilson lower endpoint used for selection is not a multiple-testing or dependence correction.

The original minute-path uncertainty remains substantial, particularly for direct entries. Margin, quantity limits, liquidity and market impact remain unmodeled, and old-contract data gaps use explicitly labeled last-mark rollover liquidation. These are conditional simulations. No new filter is automatically enabled in the paper/live configuration.

[Machine-readable results](research/results/filter_study_summary.json) contain every earlier-period candidate score, the fixed shortlist, and full selected-policy outcomes. Per-case trade journals are local in `runs/filter_study/`. Reproduce with `.\.venv\Scripts\python.exe -m research.filter_study`. Protocol SHA-256: `ed327a8c6189e6f32cea94585214d853c836b6d5ff29661ccc99c516e3bbd1d3`.

The need to account for repeated testing and data snooping is discussed in [CME's backtesting research](https://www.cmegroup.com/content/dam/cmegroup/education/files/backtesting.pdf). Selection and later-period results here remain exploratory.
