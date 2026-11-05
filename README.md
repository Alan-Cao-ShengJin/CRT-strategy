# CRT futures system

Source repository: [Alan-Cao-ShengJin/CRT-strategy](https://github.com/Alan-Cao-ShengJin/CRT-strategy).

A standalone Python implementation derived from both transcripts in this directory. It discovers Massive futures contracts, downloads historical data, backtests a shared MNQ/MES/MGC account, compares fixed entry rules, and runs persistent simulated accounts from REST bars or WebSocket trades.

The initial profitability report has been superseded after an execution audit found omitted entry-minute targets and rebreak cancellations using later bar extremes. The updated research compares explicit high-first and low-first OHLC paths. See [ANNUAL_BACKTEST.md](ANNUAL_BACKTEST.md) for the wider study and its limitations.

**This is a research and paper-trading system. Massive supplies market data, not brokerage execution. The project does not submit real-money orders.** The transcript's hourly bias and price-action judgments require approximations; the included implementation and initial backtests do not substantiate its advertised win rate.

## Start here

PowerShell, from this directory:

```powershell
# Already installed in .venv in this workspace.
# To reinstall elsewhere:
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"

.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m crt --help
```

The application reads `MASSIVE_API_KEY` from your existing `.env` or environment. It does not print the key. `.env`, downloaded data, run output, and SQLite files are excluded by `.gitignore`.

## Download and backtest

```powershell
# Find dated contracts first. Examples below were valid on 5 November 2026.
.\.venv\Scripts\python.exe -m crt contracts --date 2026-11-05

.\.venv\Scripts\python.exe -m crt download --contracts MNQZ6 MESZ6 MGCZ6 --start 2026-09-21 --end 2026-11-05 --output data/validation.csv
.\.venv\Scripts\python.exe -m crt schedules --start 2026-09-20 --end 2026-11-06 --output data/schedules.json

.\.venv\Scripts\python.exe -m crt backtest --data data/validation.csv --start 2026-09-28 --end 2026-11-05 --schedule-file data/schedules.json --output runs/validation
Start-Process .\runs\validation\report.html

.\.venv\Scripts\python.exe -m crt compare --data data/validation.csv --start 2026-09-28 --end 2026-11-05 --window-days 14 --schedule-file data/schedules.json --output runs/comparison
```

Downloads use inclusive/exclusive **UTC calendar dates**. Backtest `--start` and `--end` use **CME trading dates**, with the evening session assigned to the following day. Earlier bars warm up indicators. Forward windows retain the same strategy parameters and reset account capital independently; there is no automatic search for profitable settings. These are fixed-window evaluations, not an independently established out-of-sample investment result.

Each report directory contains `report.html`, `summary.json` with the exact configuration, `trades.csv`, `equity.csv`, and `events.jsonl`. `bar_model` defaults to `high_first`; repeat with `--bar-model low_first`. These are assumptions about intrabar ordering. The old `legacy` model is solely for reproducing archived results.

To reproduce the annual transcript audit:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[research,test]"
.\.venv\Scripts\python.exe -m research.download_annual
.\.venv\Scripts\python.exe -m research.annual_study
Start-Process .\runs\annual_audit\report.html
```

The download uses a fixed dated-contract roll calendar and can take several minutes under a five-call-per-minute API limit. `research/protocol.json` declares all cases before execution; `research/results/annual_summary.json` stores all outcomes, chronology splits, monthly figures, input hashes and uncertainty estimates. Full price data and per-case trade reports remain local.

The [1% and 2% equity-risk comparison](RISK_SIZING.md) reruns those cases with compounding and whole-contract sizing. Reproduce it with `.\.venv\Scripts\python.exe -m research.risk_sizing_study`. It removes the old dollar and contract caps and does not enforce broker margin or liquidity limits; contract-end data-gap assumptions are disclosed in the results.

[MNQ filter research](FILTER_RESEARCH.md) compares direct and rebreak entries and screens ten predefined single filters on the earlier period before fully resimulating the shortlist. Reproduce it with `.\.venv\Scripts\python.exe -m research.filter_study`. The later-period check is retrospective because that year was inspected previously; no shortlisted filter improved later win rate under both bar paths, and no new filter was enabled in the paper configuration.

[Topstep evaluation timing](TOPSTEP_EVALUATION.md) simulates MNQ rebreak at up to 1% nominal equity risk in a standard $150K Combine using the current rule snapshot, including intraday loss-floor touches, end-of-day trailing, consistency, contract caps and earlier closing. Twelve monthly starting dates per OHLC path include failures and missing-data censoring. Reproduce with `.\.venv\Scripts\python.exe -m research.topstep_evaluation`.

[CHoCH research](CHOCH_RESEARCH.md) tests confirmed counter-trend swing breaks with next-open and confirmed-retest entries on MNQ, MES and MGC. All 48 declared cases and six matched CRT baselines are retained, including timeframe/target sensitivity, costs, pivot-confirmation timestamps and missing-data exit assumptions. Reproduce with `.\.venv\Scripts\python.exe -m research.choch_study` and the merge command in the report. The CHoCH implementation is a research engine.

[Expanded CHoCH research](CHOCH_BREADTH.md) covers 17 markets (eight micro futures, BTC/USD spot and eight single stocks), eight timeframes and 544 declared simulations. Only five of 75 sufficiently sampled primary retest combinations passed the historical consistency screen; another 61 had insufficient trades. The report includes every outcome, costs, cash-asset notional limits and an audited compressed trade journal. Install `.[research,test]`, then run `research.download_breadth`, `research.run_breadth --workers 4`, `research.breadth_report` and `research.breadth_audit` with `python -m`. These additional markets are isolated research definitions.

[Shared-account CHoCH research](CHOCH_PORTFOLIO.md) replays all 17 markets on common 5m rules, alongside the five previously screened combinations, at $10k/$25k/$50k. It uses one equity pool, whole-quantity sizing, 1% entry risk, 2% aggregate open risk, capital reservations and causal allocation before intraminute execution. The report gives actual portfolio trade frequency and daily account returns, with the hindsight-selected basket and static margin assumptions explicitly identified. Reproduce with `python -m research.run_portfolio --workers 3` and `python -m research.portfolio_report` after acquiring the breadth data. This remains a research simulator.

## Your current Massive access

On 5 November 2026, authenticated futures REST requests succeeded for all three instruments. WebSocket authentication explicitly reported that the plan did not include WebSocket access. REST's newest available bar was approximately eight hours behind the wall clock during an open market. The system checks freshness and refuses to label that as current market data.

```powershell
.\.venv\Scripts\python.exe -m crt probe --contracts MNQZ6 MESZ6 MGCZ6 --transport rest

# An eight-hour delayed simulation using the REST access currently available:
.\.venv\Scripts\python.exe -m crt paper --contracts MNQZ6 MESZ6 MGCZ6 --transport rest --delay-seconds 28800 --journal runs/paper_delayed.sqlite --output runs/paper_delayed

# With a WebSocket-entitled, real-time futures feed:
.\.venv\Scripts\python.exe -m crt paper --contracts MNQZ6 MESZ6 MGCZ6 --transport websocket --feed realtime --journal runs/paper_ticks.sqlite --output runs/paper_ticks
```

REST mode polls completed minutes and uses the configured assumed OHLC path. It can backfill missed minutes on restart. WebSocket mode observes actual trade sequencing and can resolve rebreaks within the same minute. Neither mode models order-book queue priority, partial fills, or bid/ask execution. There is no automatic upgrade or purchase.

REST requests share a local rate limiter across processes, spaced 13 seconds apart by default, with HTTP 429 retries. Initialization can take a few minutes. Set the nonsecret environment variable `MASSIVE_REQUEST_INTERVAL_SECONDS` only if your actual plan permits a different rate. With limited request capacity, run one data workflow at a time.

## Configuration and controls

Use `--config config.json` with backtest, compare, or paper commands. Defaults are illustrative account limits: $50,000 initial capital, at most $100 planned risk per trade, $200 total open risk, $300 daily loss, $2,000 peak-to-current equity drawdown, two trades per CME trading day, and one simultaneous equity-index position. Actual fills can exceed planned risk when prices gap. Per-contract costs default to an **assumed** $0.85 per side plus one adverse tick; replace these with your broker's full costs.

`entry_mode` accepts `direct`, `rebreak`, or `auto`. `neutral_policy: "skip"` requires an hourly bias; `"first"` permits the transcript's neutral-bias variation. Use `schedule_file` or `--schedule-file` to require downloaded exchange open intervals and flatten five minutes before exchange closes. Without it, only the standard weekday/maintenance schedule is known. Keep schedule coverage current: missing dates block entries.

News is user-supplied: set `blackout_file` to a JSON file following [examples/blackouts.json](examples/blackouts.json). The file accepts `event_time` with configured lead/lag minutes, or explicit `start`/`end`. No live economic calendar is included. `skip_mondays` is optional and defaults to false because the transcripts treat it as discretionary.

Create a file named `STOP` in the working directory to halt entries and exit simulated positions on subsequent observed prices. Remove it before starting a new account. Ctrl+C stops processing and preserves open paper positions. A stopped process cannot execute exits. REST resume replays its journal and backfills bars; tick-mode resume halts entries and closes recovered positions on the next fresh print because missing tick history cannot safely be reconstructed. Start a fresh journal to begin another tick simulation. Configuration/calendar changes require a new journal.

Contract changes reset signal history and are rejected while a position in the old contract is open. Download one dated contract per root per file and split rollover periods. The system does not infer a liquid front month from ticker ordering, stitch adjusted continuous futures, or transfer an open position across expiries. Paper mode blocks equity contracts within seven days of expiry and MGC near its delivery month; these are conservative application rules, not a broker delivery calendar.

## Input formats and internals

Minute CSV header: `timestamp,symbol,contract,open,high,low,close,volume`. Timestamps are UTC-aware **bar starts**, such as `2026-11-03T14:30:00Z`. Off-tick prices, duplicate bars, overlapping contracts, and naive timestamps fail validation. Missing bars are not fabricated; incomplete higher-timeframe buckets are discarded.

Tick replay accepts JSONL with `symbol,time,price,size,contract,sequence` and `backtest --ticks --data ticks.jsonl`. Ticks must be in chronological order in the execution engine; the file runner sorts across inputs. Sequence IDs are strongly recommended for persistent feeds.

Core modules: `strategy.py` handles signal formation and causal indicators; `engine.py` handles shared-account sizing and simulated execution; `massive.py` handles authenticated data access; `paper.py` handles feed clocks and polling; `journal.py` persists deterministic inputs without executable serialized objects.

Read [STRATEGY.md](STRATEGY.md) for the transcript-to-code decisions and [RESEARCH.md](RESEARCH.md) for verified market and API sources.
