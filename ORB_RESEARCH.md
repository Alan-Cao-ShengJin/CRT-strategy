# Opening-range breakout research: first findings and data readiness

Research date: **6 November 2026**. This stage reviewed the original methodology, checked historical universe access and implemented tested feature/data-preparation tools. **No new ORB portfolio backtest has been completed or profitability established.**

## What the source actually tests

Zarattini, Barbon and Aziz, [A Profitable Day Trading Strategy for the U.S. Equity Market](https://www.alexandria.unisg.ch/server/api/core/bitstreams/3c2989c4-688d-4d78-8a71-f02690990d51/content), first version February 2024, reports:

| Item | Source methodology / reported result |
|---|---|
| History | 2016-2023; approximately 7,000 stocks, including delisted names |
| Opening range | First five regular-session minutes; trade only its candle direction |
| Basic eligibility | Opening price >$5, prior 14-day average volume >=1 million, prior daily ATR >$0.50 |
| Activity ranking | Opening-five-minute volume divided by its prior 14-session average; ratio >=1; top 20 |
| Exit | Stop 0.1 daily ATR from entry; otherwise session end; no fixed profit target |
| Portfolio assumptions | $25,000 initial equity, up to 4x leverage, $0.0035/share commission |
| Plain ORB | Reported annualized return 3.2%, Sharpe 0.48 |
| Relative-volume version | Reported annualized return 41.6%, Sharpe 2.81, maximum drawdown 12% |

Its selector uses volume as a proxy for activity; verifying a particular news article is not an explicit requirement. These are the authors' backtest claims, not our independently reproduced results. The institution-hosted and author-hosted copies were inspected; we anchor this review to the institution-hosted methodology and Table 2.

## Implications for the requested daily return

Using the reported 41.6% annualized figure, `(1.416**(1/252)-1)*100` is approximately **0.138% per trading day as a geometric equivalent**. This is a mathematical illustration, not the paper's observed arithmetic daily average. It does not support expecting 1%-2% daily account returns.

The relative-volume selection is a substantive hypothesis, not a minor cosmetic filter: the paper's plain and selected portfolios behave very differently. We need to test selection and execution jointly, with an unchanged baseline and explicit controls, instead of using whichever ranking produces a desired return.

## Replication decisions that still need to be resolved

- **Allocation and risk denominator.** The text describes 1% risk on the capital allocated to a position. It does not justify assigning 1% of total account equity to each of 20 simultaneous orders. Our proposed interpretation uses equal daily allocations and a 1% aggregate planned budget; it must be disclosed and compared consistently. Exact author-code allocation has not been verified.
- **Trade timing.** Ranking all opening volumes requires their completed five-minute observations. Orders must not use the opening candle's final volume or high before it closes. Latency and gap-through entry prices need explicit treatment.
- **Execution.** Entry-minute stops must remain active. A stop touched before entry is different from one touched afterward; minute OHLC leaves some paths unknown. Check both explicit paths, actual opening gaps and adverse fills. A single tick can be material relative to a tight ATR stop.
- **Costs and shorts.** A commission assumption is not a measured spread/slippage model. Historical borrow availability, locates, financing and broker buying power are not established by the paper's headline result. These need separate limitations or scenarios.
- **Share units.** Daily ATR and intraday execution must use compatible split treatment. Historical features should use only events executed by the decision date. We have not yet verified the basis of a downloaded S3 object against unadjusted REST bars.
- **Universe and annualization.** A small current-stock sample cannot reproduce a broad historical universe. Recompute annualized returns, trade win rate and day win rate from the resulting ledger instead of importing source labels or assuming their denominators.

A [public independent implementation](https://github.com/hsaeed22058/strategy-backtest) documents limitations in its own simulator, including entry-bar stop omissions, within-bar information timing and incomplete delisting coverage. We inspected its disclosures, not independently verified its reported performance or ran its code. Those admissions motivate checks in our implementation; they are not evidence that the original paper made the same errors.

## Work completed locally

**Historical universe preparation:** the existing REST subscription returned these previous-month-end common-stock snapshots for primary NYSE/Nasdaq listings:

| Effective month | Snapshot date | Common stocks |
|---|---|---:|
| August 2026 | 31 July 2026 | 5,076 |
| September 2026 | 31 August 2026 | 5,085 |
| October 2026 | 30 September 2026 | 5,088 |
| November 2026 | 31 October 2026 | 5,075 |

We also queried TWTR as of 3 January 2022: `active=true` returned the historical listing and `active=false` returned no listing. This is one practical check that the historical query is not simply today's active ticker list. It does not independently certify completeness of the security master.

Monthly membership is a declared causal adaptation: a new listing can wait until the next snapshot. It is not the exact CRSP universe used by the paper, and it is not a claim of complete removal of survivorship or reference-data revision bias. The underlying provider records remain in ignored local data; compact counts and hashes are retained in [the preparation record](research/results/orb_preparation.json).

The existing CHoCH stock sample and Humble gapper caches are useful research inputs, but neither is a complete all-market opening-volume panel. Reusing only stock-days already selected by another strategy could change both the opportunity set and the relative-volume ranking. Those subsets have not been presented as a replication of this paper.

**Feature code:** [research/orb_features.py](research/orb_features.py) computes opening relative volume and an explicitly chosen arithmetic-mean ATR from prior completed sessions. It normalizes historical price and volume inputs for executed share events, rejects incomplete histories, excludes current/future daily data, and provides deterministic relative-volume and prior-volume ranking.

**Data code:** [research/orb_data.py](research/orb_data.py) prepares historical membership and can fetch one S3 minute object, reduce it into compressed daily/opening summaries, and retain a clearly labelled three-stock parsing sample. The sample is not a ranked trade universe. Duplicate ticker-days and invalid prices are rejected; regular-session and early-close boundaries are calendar-based. A separate command can compare sampled bars with REST `adjusted=false`.

The reducer and feature functions are preparation components. A complete rolling all-market feature store, selected-stock intraday extraction, shared-account execution engine and performance audit are still required for an ORB backtest. The existing CHoCH configuration has not been changed.

## S3 access and bounded storage

You confirmed that the subscription includes S3 access. The separate access-key ID/secret have not been located, so **no authenticated S3 download has been attempted**. The file-path question remains pending. Our loader accepts explicitly named Massive S3 variables; it will not send unrelated generic AWS credentials or treat the REST API key as an S3 secret.

[Massive's official setup](https://massive.com/docs/flat-files/quickstart) specifies `https://files.massive.com`, bucket `flatfiles`, and separate S3 credentials. The [minute dataset](https://massive.com/docs/flat-files/stocks/minute-aggregates) is a daily all-market object. The current research budget caps the local workspace at 2 GiB, a raw object at 512 MiB, and requires 8 GiB free before starting. Raw scratch files are removed only after successful extraction and durable manifests. There is no bulk-download job running.

Install optional dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install --no-cache-dir -e ".[research,orb,test]"
```

Put the following names in a gitignored local file; keep real values out of chat and Git:

```dotenv
MASSIVE_S3_ACCESS_KEY_ID=your_s3_access_key_id
MASSIVE_S3_SECRET_ACCESS_KEY=your_s3_secret_access_key
```

After that file is identified, the first checks are:

```powershell
.\.venv\Scripts\python.exe -m research.orb_data --fetch-one 2024-06-07 --env-file .env.research-data
.\.venv\Scripts\python.exe -m research.orb_data --validate-basis 2024-06-07
```

That date precedes the 10 June 2024 NVIDIA split shown in [Massive's corporate-action example](https://massive.com/knowledge-base/article/does-massive-support-normal-and-reverse-splits). It is a useful share-basis control, not a strategy-selection date. A successful check would verify sample agreement, not prove that every stock/day is correct. It does not calculate trading returns.

The [initial protocol](research/orb_research_protocol.json) specifies an August-November 2026 feasibility window, warmup, universe cadence, candidate rules and outstanding execution decisions. It is a research/data-readiness design, not a claim that a final backtest specification or profitable system is complete.
