# Initial validation

**Superseded as profitability evidence.** A subsequent execution audit found that the legacy model omitted entry-minute targets and could cancel valid rebreak winners using a later high/low. The results below remain an archive of what that implementation produced; they do not establish the strategy's true profitability. The corrected study compares both explicit minute-bar paths and is documented in `ANNUAL_BACKTEST.md`.

Evaluation performed on 5 November 2026. These are recorded research results, not performance targets.

- Inputs: 45,420 MNQZ6 minute bars, 45,420 MESZ6 bars, and 45,419 MGCZ6 bars downloaded from Massive, starting 21 September 2026 UTC.
- Trading interval: CME trading dates 28 September through 4 November 2026. Earlier observations only warm indicators.
- Source: actual dated-contract futures aggregates, with 630 product schedule events covering the period. Most gaps are exchange closures; missing minutes are not synthesized.
- Parameters: defaults in `config.json`, with the downloaded exchange schedule enabled. No parameters were chosen to maximize these results.
- Account: $50,000 initial capital, $100 maximum planned trade risk, $0.85 assumed per-contract per-side cost, one adverse tick of slippage, two daily entries, and one simultaneous MNQ/MES position.
- Execution: conservative minute model. It cannot establish actual intrabar order sequencing or emulate the speaker's discretionary hourly analysis.

The full-period rebreak run had **21 trades, 4 wins (19.05%), net P&L −$959.45, and profit factor 0.326**. Account equity ended around $49,040.55. Its detailed trades, events, configuration and marked equity are in `runs/validation/`.

Fixed-rule windows, each starting a fresh account:

| Trading dates (end exclusive) | Direct trades | Direct net USD | Rebreak trades | Rebreak net USD |
|---|---:|---:|---:|---:|
| 28 Sep – 12 Oct | 13 | −964.50 | 9 | −371.05 |
| 12 Oct – 26 Oct | 14 | −1,030.70 | 5 | −32.20 |
| 26 Oct – 5 Nov | 13 | −848.35 | 7 | −556.20 |

The fixed-window account resets mean window totals need not match the uninterrupted full-period run. Reports are in `runs/comparison/`. Neither entry mode demonstrated profitability in these samples. The sample is small, the bias/chop components are explicit approximations, and minute fills penalize ambiguous sequences. These results neither verify the transcript's claims nor establish a universal result about discretionary CRT trading.

Software verification includes pattern symmetry, sweep/close edge cases, higher-timeframe candle completeness, DST/session boundaries, C3-only entries, actual-tick and separate-minute rebreak paths, gap-limited entries, adverse stop fills, nominal R:R rounding, fees and sizing, correlated equity limits, news/exchange closures, risk halts, input validation, contract-roll protection, journal idempotency/replay, and REST restart backfills.

Final verification: **45 automated tests passed**. The HTML report was rendered at 1440px and 390px widths; cards and page content fit without page overflow. An actual Massive REST paper initialization completed historical warmup, journal persistence, heartbeat reporting and clean shutdown with an explicit 28,800-second delay. Its delayed clock was in the exchange maintenance break, so it produced no new bars or trades; trade execution and restart backfilling were separately exercised with deterministic fixtures. The smoke report is in `runs/rest_smoke/`.

Massive authentication and historical bars were checked with the user's existing key without printing it. WebSocket access was refused by the subscription, and the newest available REST data was observed about eight hours behind during an open market. The software supports explicit delayed simulations and refuses stale data presented as current. No brokerage orders or subscription changes were made.
