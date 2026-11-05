"""Publish all predeclared cases, without selecting a profitable variant."""
import hashlib
import json
import shutil
from pathlib import Path


def amount(value):
    return ("+" if value >= 0 else "−") + f"${abs(value):,.0f}"


def span(values, formatter=amount):
    low, high = min(values), max(values)
    return formatter(low) if abs(high-low) < 1e-8 else formatter(low) + " to " + formatter(high)


def main():
    source = Path("research/results/annual_summary.json")
    data = json.loads(source.read_text())
    rows = data["results"]
    if len(rows) != 24:
        raise ValueError("The full 24-case study must finish before publishing a conclusion")
    for row in rows:
        row["target_hits"] = row["reasons"].get("target", 0)
        row["target_hit_rate"] = row["target_hits"]/row["trades"] if row["trades"] else None
    data["schedule_sha256"] = hashlib.sha256(Path("data/annual/schedules.json").read_bytes()).hexdigest()
    source.write_text(json.dumps(data, indent=2), encoding="utf-8")
    if Path("runs/annual_audit/comparison.png").exists():
        shutil.copyfile("runs/annual_audit/comparison.png", "research/results/comparison.png")
    table, chronology = [], []
    pairs = {"positive_both_paths": 0, "negative_both_paths": 0, "changes_sign": 0}
    for symbol in data["protocol"]["symbols"]:
        for filters in data["protocol"]["filters"]:
            for mode in data["protocol"]["entry_modes"]:
                cases = [r for r in rows if r["symbol"] == symbol and r["filters"] == filters and r["entry_mode"] == mode]
                pnl = [r["net_pnl"] for r in cases]
                key = "positive_both_paths" if min(pnl) > 0 else "negative_both_paths" if max(pnl) < 0 else "changes_sign"
                pairs[key] += 1
                table.append(f"| {symbol} | {'Mechanical' if filters == 'mechanical' else 'Bias/chop proxy'} | {mode} | {span([r['trades'] for r in cases], lambda x: str(int(x)))} | {span([r['win_rate'] for r in cases], lambda x: f'{x:.1%}')} | {span(pnl)} | {span([r['cost_stress_net_pnl'] for r in cases])} |")
                if mode == "rebreak":
                    chronology.append(f"| {symbol} | {'Mechanical' if filters == 'mechanical' else 'Bias/chop proxy'} | {span([r['chronological_early']['net_pnl'] for r in cases])} | {span([r['chronological_late']['net_pnl'] for r in cases])} |")
    total_bars = sum(x["bars"] for x in data["coverage"].values())
    includes_zero = sum(r["daily_block_bootstrap_95_net_pnl"][0] <= 0 <= r["daily_block_bootstrap_95_net_pnl"][1] for r in rows)
    maximum_win_rate = max(r["win_rate"] for r in rows)
    maximum_target_rate = max(r["target_hit_rate"] for r in rows)
    note = f'''# CRT transcript profitability audit

Completed on 5 November 2026. Evaluation: **3 November 2025 through 4 November 2026**, with earlier data for warmup. The supplied Massive key provided minute bars; requests for one-second aggregates and historical trades each returned HTTP 403. No subscription was changed.

The study used **{total_bars:,} minute bars across 17 dated contracts and 4,828 exchange schedule events**. It compares all 24 predeclared cases: three instruments × two entry methods × mechanical/proxy filters × two assumed OHLC orderings. Parameters were fixed before the annual run; they were not optimized for profit.

## What the test says

Across the 12 instrument/rule combinations, {pairs['positive_both_paths']} were profitable under both assumed paths, {pairs['negative_both_paths']} lost under both, and {pairs['changes_sign']} changed sign. These two paths are sensitivities, not observed trade histories or rigorous profit bounds. The maximum simulated net-positive win rate across all cases was {maximum_win_rate:.1%}; the highest target-hit rate before considering whether fees consumed the profit was {maximum_target_rate:.1%}. Neither reproduced the advertised 70–80%. The illustrative daily-block 95% bootstrap interval crossed zero in {includes_zero} of 24 cases.

This can identify mechanical variants that made money **in the tested simulations**. It cannot establish that the trader's full discretionary approach is profitable: the transcripts do not give an objective algorithm for hourly levels, rejection strength, chop, news selection, or switching between direct and rebreak entries. The proxy cases test our approximation of those decisions, not a verified record of his trades.

## All results after assumed costs

Dollar ranges below are the results of high-first and low-first minute paths. Each instrument has an **independent $50,000 simulated account**, at most $100 planned risk per trade, whole micro contracts, and a maximum of ten contracts. The scenarios are alternatives and must not be added together. The global two-trade daily cap is applied separately to each instrument account.

| Instrument | Filters | Entry | Simulated trades | Win rate | Net P&L | Net at higher fees |
|---|---|---|---:|---:|---:|---:|
{chr(10).join(table)}

![All predeclared CRT cases after assumed costs](research/results/comparison.png)

Base fees are an **assumed $0.85 per side per contract**, with one adverse tick at entry and market/stop exits. Higher fees reprice the same fills at $1.50 per side; they do not resimulate order sizing. Gross P&L, fee totals and commission break-even points are in the summary JSON. No broker margin model is included.

## Chronological stability of rebreak entries

The split below was declared before this annual run. It is a descriptive chronology comparison, not a clean unseen holdout: earlier work had already inspected part of September–November 2026. No rules were fitted on either side of the split.

| Instrument | Filters | 3 Nov 2025–30 Jun 2026 | 1 Jul–4 Nov 2026 |
|---|---|---:|---:|
{chr(10).join(chronology)}

## Fidelity and limitations

- C1/C2 are adjacent completed 15/30/60-minute candles. C2 sweeps one C1 extreme and closes inside C1; double sweeps are excluded. Entry must occur during C3, one tick past C2's opposite extreme. Target is C1's opposite extreme; stop distance is reward / 1.5, rounded down to valid ticks. Pending setups expire with C3.
- Entry windows are 09:30–12:00 and 20:00–22:00 New York time, with DST. First qualified setup per instrument/session, maximum two entries per CME trading date. Positions otherwise retain their bracket, with daily/exchange-close liquidation for operational reasons.
- Mechanical cases disable invented quantitative bias/chop thresholds and permit the first setup without a directional bias, a simplified variation discussed in the interview. Proxy cases add the pre-existing, documented hourly/chop approximation. They share the same minimum one-tick stop/target eligibility, actual bracket rounding, sizing and costs.
- Minute bars do not show the real intrabar sequence. We explicitly simulate both open→high→low→close and open→low→high→close, allowing same-minute entries, rebreaks and exits. Additional oscillations, actual queue priority, partial fills, latency and bid/ask spreads remain unobserved.
- Fixed calendar rolls were specified in advance: equity index contracts switch at 18:00 New York seven calendar days before the third Friday of the delivery quarter; gold switches seven days before the delivery month. This is a research convention, not volume-based front-month selection. Prices are not back-adjusted. Signal history resets at a roll, so hourly filters must warm up again.
- Missing minute bars are not fabricated. The downloader uses dated futures, not spot prices or an adjusted continuous series. File hashes and contract windows are retained in the summary.
- The account-wide $2,000 permanent drawdown stop is disabled for the study so it does not censor the strategy sample. The daily risk/trade rules and whole-contract constraints remain. A losing case may therefore exceed the application's default permitted drawdown.
- Confidence intervals resample weekdays as daily blocks, including zero-trade days and holidays. They assume independent daily blocks and do not account for longer regime dependence, multiple comparisons, discretionary-model uncertainty, or uncertainty about actual intrabar paths. Win-rate Wilson intervals likewise do not account for dependence.

## Correction to the original report

The initial legacy backtest incorrectly omitted entry-minute targets and could cancel valid rebreak winners using a later minute high/low. Its −$959.45 / 19% win result is **superseded and unsuitable as profitability evidence**. On that same shorter sample, with its original filters and sizing, corrected high-first execution returned +$16.25 across 33 trades, and low-first returned −$53.45 across 32 trades. The wider annual study above uses independently declared mechanical and proxy cases and must not be conflated with that initial portfolio.

## Reproduce and inspect

```powershell
.\\.venv\\Scripts\\python.exe -m pip install -e ".[research,test]"
.\\.venv\\Scripts\\python.exe -m research.download_annual
.\\.venv\\Scripts\\python.exe -m research.annual_study
.\\.venv\\Scripts\\python.exe -m research.summarize_study
Start-Process .\\runs\\annual_audit\\report.html
```

The detailed local report is `runs/annual_audit/report.html`, with per-case trade CSVs, equity samples and event logs in subdirectories. [The machine-readable summary](research/results/annual_summary.json) contains all cases, monthly results, uncertainty estimates, path-sensitive trade counts and SHA-256 input hashes. The protocol hash is `{data['protocol_sha256']}`.

Contract mechanics were checked against [CME's Micro E-mini FAQ](https://www.cmegroup.com/articles/faqs/micro-e-mini-equity-index-futures-frequently-asked-questions.html) and [gold product overview](https://www.cmegroup.com/education/courses/event-contracts-underlying-markets/product-gold). Data access was checked using [Massive's aggregate documentation](https://massive.com/docs/rest/futures/aggregates) and [historical trades documentation](https://massive.com/docs/rest/futures/trades-quotes/trades). Finer-data access was tested directly and refused by the supplied key.
'''
    Path("ANNUAL_BACKTEST.md").write_text(note, encoding="utf-8")
    print(json.dumps({"completed_cases": len(rows), "total_bars": total_bars, "pair_outcomes": pairs,
                      "bootstrap_intervals_crossing_zero": includes_zero, "maximum_win_rate": maximum_win_rate}, indent=2))


if __name__ == "__main__":
    main()
