# MNQ rebreak: Topstep 150K evaluation timing at 1% risk

Current-rule historical simulation checked 5 November 2026. This tests the **standard $150,000 Trading Combine without the optional Daily Loss Limit add-on**, on MNQ mechanical rebreak only. It does not purchase, connect to, or trade a real evaluation.

## Results

| Assumed price order | Passed | Failed loss limit | Unresolved | Passing calendar days | Median among passes |
|---|---:|---:|---:|---|---:|
| high_first | 3/12 | 8/12 | 1/12 | 62, 39, 9 | 39 days |
| low_first | 2/12 | 9/12 | 1/12 | 28, 38 | 33.0 days |

These are overlapping historical cohorts, not independent probability trials. Passing times are **conditional on the attempts that passed**; failures and censored attempts have no passing time. The 90-calendar-day research horizon is not a firm deadline. Do not interpret the median among winners as an expected time until success.

One percent of nominal account equity initially risks $1,500, which is one-third of the $4,500 initial loss allowance. Successful historical windows therefore do not establish a dependable passing schedule. Calendar days below include the starting date, weekends and holidays. The JSON also records completed market sessions and days with trades.

| Start trading date | Assumed OHLC path | Outcome | Calendar days inclusive | Closed trades | P&L / last marked P&L |
|---|---|---|---:|---:|---:|
| 2025-11-03 | high_first | failed_mll | 4 | 4 | $-4,542.52 |
| 2025-12-01 | high_first | failed_mll | 31 | 13 | $-3,185.82 |
| 2026-01-02 | high_first | failed_mll | 15 | 9 | $-4,519.78 |
| 2026-02-02 | high_first | failed_mll | 16 | 10 | $-11.74 |
| 2026-03-02 | high_first | failed_mll | 2 | 4 | $-4,547.62 |
| 2026-04-01 | high_first | passed | 62 | 31 | $10,568.98 |
| 2026-05-01 | high_first | passed | 39 | 14 | $9,705.80 |
| 2026-06-01 | high_first | passed | 9 | 5 | $10,407.96 |
| 2026-07-01 | high_first | failed_mll | 16 | 7 | $-1,722.20 |
| 2026-08-03 | high_first | failed_mll | 4 | 4 | $-4,522.38 |
| 2026-09-01 | high_first | data_gap_censored | 11 | 8 | $3,803.19 |
| 2026-10-01 | high_first | failed_mll | 23 | 17 | $-2,535.42 |
| 2025-11-03 | low_first | failed_mll | 4 | 4 | $-4,542.52 |
| 2025-12-01 | low_first | failed_mll | 31 | 14 | $-1,097.28 |
| 2026-01-02 | low_first | failed_mll | 13 | 4 | $-4,568.58 |
| 2026-02-02 | low_first | failed_mll | 12 | 8 | $-423.12 |
| 2026-03-02 | low_first | failed_mll | 2 | 4 | $-4,547.62 |
| 2026-04-01 | low_first | failed_mll | 23 | 14 | $-33.76 |
| 2026-05-01 | low_first | passed | 28 | 14 | $9,772.08 |
| 2026-06-01 | low_first | passed | 38 | 21 | $9,563.78 |
| 2026-07-01 | low_first | failed_mll | 20 | 7 | $-1,722.86 |
| 2026-08-03 | low_first | failed_mll | 4 | 4 | $-4,522.38 |
| 2026-09-01 | low_first | data_gap_censored | 11 | 9 | $2,283.57 |
| 2026-10-01 | low_first | failed_mll | 14 | 10 | $-2,485.76 |

## Numerical rules modeled

- Starting nominal balance $150,000; risk up to 1% of current equity per entry (initially $1,500). Maximum 150 MNQ contracts, so narrow-stop trades may use less than 1%. Two entries per trading day, first qualified CRT per session, original 15/30/60-minute setup and 1.5 nominal R:R.
- $9,000 profit target and 55% best-day consistency. Actual profit required is max($9,000, best finalized day's net profit / 0.55). When flat and eligible, stop taking entries and wait for end-of-day confirmation, with at least two traded days.
- Initial loss floor $145,500. It rises using end-of-day cash minus $4,500, never declines, and stops at $150,000. Touching it intraday using realized plus unrealized P&L fails the attempt, even if later prices recover. Risk-floor crossing is an explicit level in each hypothetical price path.
- Position liquidation at 16:08 New York, before the listed 16:10 deadline; earlier exchange closes keep the existing five-minute buffer. Pending entries are unavailable during routine restricted news windows. The routine 08:30/14:00/14:30 New York releases do not overlap this strategy's 09:30-12:00 / 20:00-22:00 entry sessions.
- Current listed MNQ commissions/fees $1.22 round trip ($0.61 per side), plus one adverse tick on entries and stop/market exits. Apply this current snapshot counterfactually to all historical prices; older actual rules/fees may have differed.

Sources: [parameters](https://help.topstep.com/en/articles/8284197-trading-combine-parameters), [profit target/consistency](https://help.topstep.com/en/articles/8284208-consistency-at-topstep), [loss floor](https://help.topstep.com/en/articles/8284204-what-is-the-maximum-loss-limit), [hours](https://help.topstep.com/en/articles/8284206-when-and-what-products-can-i-trade), [fees](https://help.topstep.com/en/articles/8284213-topstepx-commissions-and-fees), [optional DLL](https://help.topstep.com/en/articles/10490293-daily-loss-limit-in-the-trading-combine-and-express-funded-account), [news adjustments](https://help.topstep.com/en/articles/13613539-risk-adjustments-high-risk-high-volatility).

## Interpretation and limitations

Twelve monthly starts, from November 2025 through October 2026, use the first available CME trading date in the month. Each attempt has a fresh account and indicator warmup, then is followed until passing, loss-floor failure, 90 calendar days or data end. There are no automatic resets or purchased accounts. Attempts overlap and share market regimes.

Price order within a minute is unknown. The study uses separate open-high-low-close and open-low-high-close assumptions, not reconstructed ticks. A missing required exit price while a position is open censors the attempt rather than inventing a profitable last-mark liquidation. An unfinished late cohort is not classified as a failed account.

This is a numerical eligibility model, not certification of every qualitative conduct, platform or broker rule. Emergency news, temporary account-specific restrictions, exchange price limits, real fill quality and platform failures are unmodeled. Evaluation passage is not a funded-account payout or evidence of persistent future profitability. Subscription/reset/activation expenses are external and excluded from trading P&L; a purchase decision needs those costs too.

Protocol: [topstep_eval_protocol.json](research/topstep_eval_protocol.json). Full daily balances, loss floors, trade journals, input hashes and source hashes: [topstep_evaluation_summary.json](research/results/topstep_evaluation_summary.json). Reproduce with `.\.venv\Scripts\python.exe -m research.topstep_evaluation`.
