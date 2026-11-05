# CRT returns at 1% and 2% account risk

Same data and signals as [the annual audit](ANNUAL_BACKTEST.md): 3 November 2025 through 4 November 2026. Each instrument/variant has an independent **$50,000 starting account**. Positions target **1% or 2% of current marked equity per trade**, including modeled round-trip fees and one adverse stop tick in the planned risk budget. Whole-contract rounding normally uses slightly less. Gains and losses compound; these are full reruns, not a linear multiplication of the $100-risk result.

Total return = (ending equity / $50,000 − 1) × 100. Drawdown is the maximum percentage decline from the running equity peak along each assumed OHLC path. The two numbers in each range are the two bar-order assumptions, not confidence bounds or guaranteed extrema.

| Instrument | Filters | Entry | Original fixed $100 return | Return at 1% risk | Return at 2% risk | Max drawdown at 1% | Max drawdown at 2% |
|---|---|---|---:|---:|---:|---:|---:|
| MNQ | Mechanical | direct | -0.59% to +5.60% | -6.91% to +37.03% | -18.35% to +81.35% | 11.38% to 22.27% | 22.94% to 42.39% |
| MNQ | Mechanical | rebreak | +2.39% to +4.04% | +13.39% to +21.15% | +25.74% to +43.44% | 18.87% to 20.30% | 34.74% to 38.27% |
| MNQ | Bias/chop proxy | direct | -4.08% to -1.51% | -21.04% to -9.06% | -41.27% to -20.75% | 16.83% to 24.56% | 33.54% to 46.19% |
| MNQ | Bias/chop proxy | rebreak | -0.12% to +1.00% | -0.50% to +4.72% | -5.94% to +7.68% | 11.02% to 12.29% | 21.14% to 23.91% |
| MES | Mechanical | direct | -11.38% to -9.38% | -53.65% to -48.62% | -79.48% to -75.53% | 49.13% to 53.91% | 75.86% to 79.68% |
| MES | Mechanical | rebreak | -7.65% to -5.47% | -38.38% to -31.91% | -63.65% to -53.97% | 35.27% to 41.50% | 58.67% to 67.28% |
| MES | Bias/chop proxy | direct | -10.41% to -9.99% | -46.82% to -46.37% | -72.25% to -71.80% | 47.16% to 48.52% | 72.65% to 74.00% |
| MES | Bias/chop proxy | rebreak | -6.55% to -6.22% | -30.97% to -30.15% | -52.76% to -51.69% | 30.98% to 31.79% | 52.77% to 53.83% |
| MGC | Mechanical | direct | -10.39% to -8.65% | -43.54% to -38.33% | -69.45% to -62.54% | 39.32% to 44.38% | 63.72% to 70.35% |
| MGC | Mechanical | rebreak | -0.84% to +1.48% | -7.69% to +11.71% | -20.35% to +20.00% | 10.29% to 18.22% | 21.33% to 36.91% |
| MGC | Bias/chop proxy | direct | -9.69% to -7.69% | -44.79% to -38.09% | -70.43% to -62.41% | 39.01% to 45.60% | 63.55% to 71.31% |
| MGC | Bias/chop proxy | rebreak | -2.61% to -2.12% | -18.04% to -13.52% | -34.68% to -27.14% | 16.52% to 21.02% | 31.51% to 39.01% |

The fixed $100 trade cap, $300 daily loss cap and ten-contract cap had to be removed for full percentage sizing. The two-trade daily limit, one-position limit, session rules, 1.5 nominal reward/risk and original assumed costs remain. As in the annual audit, there is no permanent account drawdown cutoff. Brokerage/exchange margin, available liquidity, market impact and broker quantity limits are **not modeled**. Tight stops can therefore produce unusually large micro-contract quantities; these are sizing simulations, not demonstrated executable returns. The maximum observed quantity across the runs was **506 contracts**. Actual reported mean/max planned risk fractions and quantities are in the JSON.

The larger risk budgets can also permit trades whose minimum whole-contract risk exceeded the old $100 limit. That can change trade counts and performance. Fees remain an assumed $0.85 per side per contract, with one adverse tick at entry and stop/market exits. Gaps can exceed planned risk. Results are alternative independent accounts; they must not be summed to claim one shared-account portfolio return.

Data-end assumption: some newly eligible positions span a gap at a dated-contract transition. The data source returned no prices for the missing old-contract interval. Those positions are marked out at the last observed old-contract price plus adverse slippage, explicitly labeled `roll_data_end_mark`; these are not verified executable fills. Such bookkeeping occurred in **16 of 48 cases**. Each affected contract, timestamp and P&L contribution is retained in the JSON. Returns and drawdowns are conditional on this additional data-gap assumption.

Minute-bar path and discretionary-model limitations remain unchanged. Mechanical cases use explicit CRT rules; proxy cases add the existing approximation of hourly bias and chop. Increasing risk does not establish an edge that was absent or uncertain in the underlying tests.

The meaning of account-equity risk and its relation to stop distance/quantity follows [CME's position-sizing explanation](https://www.cmegroup.com/education/courses/trade-and-risk-management/proper-position-size). [Margin and position-risk requirements are separate considerations](https://www.cmegroup.com/education/courses/things-to-know-before-trading-cme-futures/position-and-risk-management).

Reproduce:

```powershell
.\.venv\Scripts\python.exe -m research.risk_sizing_study
```

[Machine-readable results](research/results/risk_sizing_summary.json) contain all 48 cases, trade counts, final equity, returns, drawdowns, achieved risk fractions and full configurations. Detailed trade reports remain in `runs/risk_sizing/`.
