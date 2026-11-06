# Independent check of the video’s four-hour CHoCH strategy

**The primary reconstruction lost money on both NQ and ES. None of the seven declared interpretations maintained positive dollar profit and net R across both markets, time segments and cost assumptions. The earlier MCL five-minute retest remains the stronger paper-validation candidate.**

The video describes a different setup from our earlier five-minute retest study: confirmed four-hour change of character, immediate entry, a swing stop, a 1R target, and no required liquidity sweep. Its reported standard-contract profits are not account return percentages. Source: [AI Pathways video](https://www.youtube.com/watch?v=z97PnFIPoFg), particularly 11:46–13:15 and 14:31–14:50. The transcript does not fix pivot width, initial trend state, candle anchor, roll procedure or complete executable rules. This is an independent mechanical reconstruction, not verification of the presenter’s code or claimed results.

Evaluation: **7 April 2025 00:00 UTC to 5 November 2026 00:00 UTC**, split at **3 November 2025 00:00 UTC**. Available minute prices begin in November 2024, but the usable common calendar starts in March 2025; April allows warmup. The earlier segment was unexamined locally before this study. Both segments are historical, overlap a period used by the video, and are not pristine forward validation.

## Primary reconstruction

Strict two-left/two-right confirmed swing highs and lows establish higher-high/higher-low or lower-high/lower-low structure. Take the first opposite closing break by a tick; enter the next available minute open; stop a tick beyond the latest confirmed opposite swing and target 1R from the slipped entry. One position per instrument; positions may stay overnight. Four-hour candles start at the exchange session open, normally 18:00 New York, with a final shorter candle. A stop and target both touched in one minute count as a stop. Actual price gaps fill at the observed open with adverse slippage.

| Market | Trades | Win rate | Net profit, one standard contract | Total net R | PF | Dollar drawdown | Higher-cost net profit |
|---|---:|---:|---:|---:|---:|---:|---:|
| NQ | 36 | 47.2% | $-15,275 | -2.03 | 0.93 | $99,770 | $-15,575 |
| ES | 42 | 42.9% | $-12,232 | -5.14 | 0.89 | $54,908 | $-12,878 |

R is trade net P&L divided by its initial stop risk in dollars. Summing R makes different stop sizes easier to compare; it is not a compounding, integer-contract account return. Dollar drawdown uses minute closes plus adverse minute extremes while positions remain open, with estimated exit costs. It does not reconstruct a tick-by-tick peak-to-trough path or broker liquidation. The fixed-contract study does not enforce margin, an account loss floor, or a 1% risk ceiling.

## All predeclared interpretations

Each row changes one primary rule. No target or stop optimization was added. Every interpretation is disclosed, including losses.

| Interpretation | NQ trades | NQ net $ | NQ net R | ES trades | ES net $ | ES net R |
|---|---:|---:|---:|---:|---:|---:|
| primary | 36 | -15,275 | -2.03 | 42 | -12,232 | -5.14 |
| pivot3 | 34 | 76,455 | +5.97 | 38 | -9,975 | -5.12 |
| pivot5 | 22 | -106,545 | -10.02 | 17 | 10,298 | -2.05 |
| bos_state | 55 | 21,975 | -3.06 | 61 | 46,198 | +1.81 |
| midnight_anchor | 50 | -20,385 | -1.26 | 45 | -24,910 | -5.69 |
| strict_coverage | 35 | -32,895 | -3.03 | 35 | 1,290 | -2.11 |
| ny_entry_8_16 | 21 | -88,830 | -5.02 | 26 | -50,298 | -11.10 |

Positive dollar profit in both markets over the full period: **1/7** (bos_state). Positive dollar profit **and** net R in both time segments, both markets and both cost assumptions: **0/7** (none). These overlapping interpretations are correlated sensitivity checks, not independent confirmations or a statistical significance test.

| Interpretation | Market | Early net $ | Early net R | Recent net $ | Recent net R |
|---|---|---:|---:|---:|---:|
| primary | NQ | -11,025 | -4.02 | -4,250 | +1.98 |
| primary | ES | 18,360 | -0.50 | -25,232 | -4.10 |
| pivot3 | NQ | 13,545 | -1.01 | 62,910 | +6.98 |
| pivot3 | ES | 10,148 | -1.49 | -9,258 | -2.08 |
| pivot5 | NQ | 31,505 | -2.01 | -138,050 | -8.01 |
| pivot5 | ES | 21,815 | -1.02 | -11,518 | -1.03 |
| bos_state | NQ | -20,165 | -8.03 | 42,140 | +4.97 |
| bos_state | ES | 13,198 | -5.08 | 33,000 | +6.89 |
| midnight_anchor | NQ | 18,600 | -1.25 | -48,930 | -1.23 |
| midnight_anchor | ES | 5,038 | -2.06 | -29,948 | -3.63 |
| strict_coverage | NQ | -5,560 | -4.02 | -27,335 | +0.99 |
| strict_coverage | ES | 24,392 | +1.52 | -17,742 | -3.09 |
| ny_entry_8_16 | NQ | -38,145 | -4.01 | -50,685 | -1.01 |
| ny_entry_8_16 | ES | -318 | -1.48 | -30,920 | -7.08 |

Each segment starts flat and closes residual positions at its final available close; split results need not sum to the uninterrupted full-period result.

## Micro sizing and execution limits

MNQ/MES figures below are **price-path proxies using standard NQ/ES bars**, with micro multipliers and micro fees. They are not backtests of actual micro fills. No account return or required account balance is established by these fixed-size results.

| Primary proxy | Net profit, one micro | Median stop risk, one micro | Largest stop risk | Trades/day |
|---|---:|---:|---:|---:|
| MNQ_proxy | $-1,571.90 | $1,141.75 | $2,309.00 | 0.088 |
| MES_proxy | $-1,276.05 | $453.75 | $1,277.50 | 0.102 |

At a 1% risk ceiling, one micro contract needs account equity at least **100 times the trade’s dollar stop risk**, before margin and gap risk. A small account must skip signals whose minimum contract size exceeds that budget. The video’s standard-contract dollar profits cannot be scaled into a small account by assuming fractional futures contracts.

Assumed costs: standard $2.50/side or micro $0.85/side, one adverse tick on market entries, stop/data-end exits, and both roll legs. Stress doubles commissions and uses two adverse ticks; all executions rerun. These are modeling assumptions, not broker fee quotations. The gross P&L field already includes entry/exit market slippage; the costs field contains commissions plus roll slippage.

## Data and audit

Dated quarterly contracts switch at 16:00 New York on the Thursday eight calendar days before third Friday. Simultaneous old/new opens establish a causal spread offset for signal coordinates. Open positions pay for both roll legs. No artificial spread jump is booked as profit. Require at least 95% observed scheduled minutes and no missing scheduled run longer than five minutes for a signal candle; strict-coverage sensitivity requires every minute. Rejected buckets reset structure; no OHLC is invented. A missing bar can mean no trades or incomplete history, which this feed alone cannot distinguish.

Parse calendar events chronologically: holiday settlement labels may combine multiple sessions. A later pre-open bounds an omitted close; it does not prove the market remained tradable up to that time. Earlier unreported halts produce missing-minute rejection. Saturday testing events are excluded. Some overnight outage periods and sparse new-contract roll minutes therefore remain coverage limitations. One conflicting ES minute in January 2025 was quarantined rather than choosing a duplicate; it precedes evaluation.

| Market | Primary accepted/rejected buckets, including available warmup | Trades with absent scheduled minutes while held | Largest such gap, minutes |
|---|---:|---:|---:|
| NQ | 2504 / 31 | 6 | 119 |
| ES | 2714 / 151 | 11 | 4 |

Calendar coverage counts differ before the common evaluation window. There are no observed evaluation-period price minutes outside the parsed calendars in the acquired snapshots. Orders through gaps execute only at the next observed price; unobserved trading paths are not demonstrated fills.

Audit: **168 cases / 4,168 simulated trade records** reconciled against cached entry opens, causal pivot times, 1R brackets, roll charges, fees, net R and final equity. These records include overlapping periods, sensitivities and proxies, not that many independent trades. The unit tests cover pivot confirmation, prefix causality, DST and final shorter candles, scheduled halts, holiday labels, unexpected gaps, stop-first fills and roll continuity.

Primary references: [Massive aggregate semantics](https://massive.com/docs/rest/futures/aggregates), [CME NQ specifications](https://www.cmegroup.com/markets/equities/nasdaq/e-mini-nasdaq-100.contractSpecs.html), [CME ES specifications](https://www.cmegroup.com/markets/equities/sp/e-mini-sandp500.contractSpecs.html), [CME trading hours and Saturday testing calendar](https://www.cmegroup.com/trading-hours.html).

## Where the stronger evidence remains

The existing **MCL five-minute confirmed-retest / 2R** candidate remains a separate strategy. Its same-date May–November check on a $50,000 account at 1% planned risk returned **+25.18%**, with **59 trades**, **52.54% wins**, **PF 2.02**, **4.90% minute-mark drawdown**, and **+$11,277** under fixed-fill higher-cost repricing. Mean daily return was **0.171%** across 134 dates; it did not trade every day. See [65-market comparison](CHOCH_UNIVERSE65.md) and [training-based selection](CHOCH_POOL_SEARCH.md). That candidate came from a broad historical search, so selection bias and dependence on one market regime remain. It is a paper-validation candidate, not established future income.

Adding all 65 markets produced only **+3.82%**, with **19.56% drawdown** and negative stressed P&L over the same dates. The evidence favors testing a narrow candidate rather than assuming more assets or more trades will improve returns. None of these studies supports a dependable single-digit percentage return every day. Do not replace the paper watchlist with the best-looking four-hour variant after this retrospective comparison.

Reproduce: `python -m research.four_hour_download`, `python -m research.four_hour`, `python -m research.four_hour_report`. Install the repository research/test extras first. Raw vendor data and the full source transcript stay in ignored local directories. Protocol, source hashes, input hashes, all summaries, audited compressed journals and the audit are retained.
