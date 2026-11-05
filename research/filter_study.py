"""Train-only filter screening followed by full chronological resimulation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from crt.data import batches, read_bars
from crt.models import NY, timestamp
from crt.report import write_report
from crt.strategy import trading_day
from research.risk_sizing_study import EquityRiskEngine, fractional_config

PROTOCOL_FILE = Path("research/filter_protocol.json")


def setup_features(setup, c1, c2, chop, hourly_bias):
    local = setup.created.astimezone(NY)
    span = c2.high-c2.low
    close_strength = (c2.close-c2.low)/span if setup.side == 1 else (c2.high-c2.close)/span
    swept = c1.low-c2.low if setup.side == 1 else c2.high-c1.high
    return {"available_at": setup.created.isoformat(), "timeframe": setup.timeframe,
            "session": setup.session.split(":", 1)[1], "local_minute": local.hour*60+local.minute,
            "local_weekday": local.weekday(), "directional_body": setup.side*(c2.close-c2.open) > 0,
            "close_strength": close_strength, "sweep_fraction": swept/(c1.high-c1.low),
            "efficiency": chop.efficiency, "reward_points": setup.side*(setup.target-setup.trigger),
            "hourly_aligned": hourly_bias == setup.side}


def passes(name, f):
    checks = {
        "baseline": True,
        "ny_only": f["session"] == "new_york",
        "ny_first_hour_setups": 570 <= f["local_minute"] < 630,
        "fifteen_minute_only": f["timeframe"] == 15,
        "directional_c2_body": f["directional_body"],
        "strong_rejection": f["close_strength"] >= .65,
        "meaningful_sweep": f["sweep_fraction"] >= .10,
        "efficient_price_action": f["efficiency"] >= .30,
        "minimum_reward_12_points": f["reward_points"] >= 12,
        "hourly_bias_aligned": f["hourly_aligned"],
        "skip_mondays": f["local_weekday"] != 0,
    }
    return checks[name]


class FilterEngine(EquityRiskEngine):
    def __init__(self, config, rule="baseline"):
        super().__init__(config)
        self.rule = rule
        self.features = {}

    def _accept_setup(self, setup, c1, c2, chop):
        hourly = self.states[setup.symbol].bias.get(setup.created)
        features = setup_features(setup, c1, c2, chop, hourly)
        self.features[setup.id] = features
        return passes(self.rule, features)


def run(config, bars, protocol, rule):
    engine = FilterEngine(config, rule)
    for batch in batches(bars):
        day = trading_day(batch[0].start)
        if day >= protocol["validation_end_exclusive"]:
            break
        # Preserve the sizing study's explicit old-contract data-end bookkeeping.
        for b in batch:
            st = engine.states[b.symbol]
            if st.contract is not None and st.contract != b.contract and b.symbol in engine.positions:
                engine._exit(b.symbol, engine.marks[b.symbol], st.last_bar.end, "roll_data_end_mark", market=True)
                engine.roll_data_liquidations.append({"contract": st.contract, "time": st.last_bar.end.isoformat(), "net": engine.trades[-1].net})
                engine._risk_check(st.last_bar.end)
                engine.curve.append({"time": st.last_bar.end.isoformat(), "equity": engine.equity})
        engine.on_bar_batch(batch, learn_only=day < protocol["start"])
    engine.finish()
    engine._risk_check(engine.last_time)
    return engine


def wilson_lower(wins, n):
    if not n:
        return 0.
    z = 1.959963984540054
    p = wins/n
    return (p+z*z/(2*n)-z*((p*(1-p)+z*z/(4*n))/n)**.5)/(1+z*z/n)


def trade_stats(trades, starting_equity=50000):
    n = len(trades)
    wins = sum(t.net > 0 for t in trades)
    net = sum(t.net for t in trades)
    positive = sum(max(t.net, 0) for t in trades)
    negative = -sum(min(t.net, 0) for t in trades)
    return {"trades": n, "wins": wins, "win_rate": wins/n if n else None, "net_pnl": net,
            "net_r_per_trade": sum(t.r_multiple for t in trades)/n if n else None,
            "profit_factor": positive/negative if negative else None,
            "return_pct": net/starting_equity*100, "starting_equity": starting_equity,
            "wilson_lower": wilson_lower(wins, n)}


def select(screening, mode, protocol):
    eligible = []
    for name in protocol["candidates"]:
        cases = [r for r in screening if r["entry_mode"] == mode and r["filter"] == name]
        if len(cases) != len(protocol["paths"]):
            continue
        if min(r["trades"] for r in cases) < protocol["minimum_training_subset_trades_each_path"]:
            continue
        eligible.append({"filter": name, "score": min(r["wilson_lower"] for r in cases),
                         "minimum_training_trades": min(r["trades"] for r in cases)})
    return sorted(eligible, key=lambda r: (-r["score"], -r["minimum_training_trades"], r["filter"]))[:protocol["shortlist_per_entry_mode"]]


def evaluate(engine, mode, path, name, protocol, output):
    early = [t for t in engine.trades if trading_day(timestamp(t.entry_time)) < protocol["selection_end_exclusive"]]
    later = [t for t in engine.trades if trading_day(timestamp(t.entry_time)) >= protocol["selection_end_exclusive"]]
    row = {"entry_mode": mode, "bar_path": path, "filter": name,
           "full_period": trade_stats(engine.trades), "training_resimulation": trade_stats(early),
           "later_validation": trade_stats(later, 50000+sum(t.net for t in early)),
           "max_drawdown_pct": engine.max_fractional_drawdown*100,
           "max_contracts": max(engine.entry_quantities, default=0),
           "roll_data_liquidations": engine.roll_data_liquidations}
    dest = output/f"{mode}_{name}_{path}"
    row["report"] = str(dest/"report.html")
    engine.curve = engine.curve[::60]+engine.curve[-1:]
    write_report(engine, dest, {"filter_study": True, "filter": name,
                               "retrospective_validation": True, "margin_modeled": False})
    return row


def main():
    protocol = json.loads(PROTOCOL_FILE.read_text())
    sizing = json.loads(Path("research/results/risk_sizing_summary.json").read_text())
    manifest = json.loads(Path("data/annual/manifest.json").read_text())
    bars = read_bars([r["file"] for r in manifest if r["root"] == protocol["symbol"]])
    configs = {(r["entry_mode"], r["bar_path"]): r for r in sizing["results"]
               if r["symbol"] == protocol["symbol"] and r["filters"] == "mechanical" and r["risk_fraction"] == protocol["risk_fraction"]}
    record = {"protocol": protocol, "protocol_sha256": hashlib.sha256(PROTOCOL_FILE.read_bytes()).hexdigest(),
              "source_risk_summary_sha256": hashlib.sha256(Path("research/results/risk_sizing_summary.json").read_bytes()).hexdigest(),
              "source_manifest_sha256": hashlib.sha256(Path("data/annual/manifest.json").read_bytes()).hexdigest(),
              "screening": [], "selection": {}, "results": []}
    output = Path("runs/filter_study")
    for mode in protocol["entry_modes"]:
        for path in protocol["paths"]:
            source = configs[(mode, path)]
            config = fractional_config(source["config"], protocol["risk_fraction"])
            engine = run(config, bars, protocol, "baseline")
            if len(engine.trades) != source["trades"] or abs(engine.equity-(50000+source["net_pnl"])) > 1e-6:
                raise RuntimeError("Instrumented baseline does not reconcile to the saved sizing study")
            row = evaluate(engine, mode, path, "baseline", protocol, output)
            record["results"].append(row)
            earlier = [t for t in engine.trades if trading_day(timestamp(t.entry_time)) < protocol["selection_end_exclusive"]]
            for name in protocol["candidates"]:
                subset = [t for t in earlier if passes(name, engine.features[t.setup_id])]
                record["screening"].append({"entry_mode": mode, "bar_path": path, "filter": name, **trade_stats(subset)})
            print(json.dumps({"stage": "baseline", "mode": mode, "path": path, "trades": len(engine.trades)}), flush=True)
        record["selection"][mode] = select(record["screening"], mode, protocol)
    # Persist the training-only shortlist before any selected policy's later results.
    target = Path("research/results/filter_study_summary.json")
    target.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps({"stage": "shortlist_frozen", "selection": record["selection"]}), flush=True)
    for mode in protocol["entry_modes"]:
        for selected in record["selection"][mode]:
            name = selected["filter"]
            for path in protocol["paths"]:
                config = fractional_config(configs[(mode, path)]["config"], protocol["risk_fraction"])
                engine = run(config, bars, protocol, name)
                row = evaluate(engine, mode, path, name, protocol, output)
                record["results"].append(row)
                target.write_text(json.dumps(record, indent=2), encoding="utf-8")
                print(json.dumps({"stage": "selected_policy", "mode": mode, "path": path, "filter": name,
                                  "training": row["training_resimulation"], "later": row["later_validation"]}), flush=True)
    write_note(record)


def write_note(record):
    rows = record["results"]
    lines = []
    def span(values, percent=False, integer=False):
        values = [v for v in values if v is not None]
        if not values:
            return "N/A"
        fmt = (lambda x: str(int(x))) if integer else (lambda x: f"{x*100:.1f}%") if percent else (lambda x: f"{x:.2f}")
        lo, hi = min(values), max(values)
        return fmt(lo) if abs(hi-lo) < 1e-9 else fmt(lo)+" to "+fmt(hi)
    for mode in record["protocol"]["entry_modes"]:
        for name in ["baseline"]+[s["filter"] for s in record["selection"][mode]]:
            cases = [r for r in rows if r["entry_mode"] == mode and r["filter"] == name]
            for period, title in (("training_resimulation", "Earlier: Nov–Jun"), ("later_validation", "Later: Jul–Nov"), ("full_period", "Full year")):
                lines.append(f"| {mode} | {name} | {title} | {span([r[period]['trades'] for r in cases], integer=True)} | {span([r[period]['win_rate'] for r in cases], True)} | {span([r[period]['net_r_per_trade'] for r in cases])} | {span([r[period]['return_pct'] for r in cases])}% |")
    improved = 0
    selected_count = 0
    for mode, candidates in record["selection"].items():
        for candidate in candidates:
            selected_count += 1
            cases = [r for r in rows if r["entry_mode"] == mode and r["filter"] == candidate["filter"]]
            baselines = {r["bar_path"]: r for r in rows if r["entry_mode"] == mode and r["filter"] == "baseline"}
            improved += all(r["later_validation"]["win_rate"] > baselines[r["bar_path"]]["later_validation"]["win_rate"] for r in cases)
    text = f'''# MNQ filter research and direct-break comparison

This is retrospective research on 3 November 2025–4 November 2026. **The whole year was already inspected in prior work. The later split is not pristine out-of-sample or prospective evidence.** It is used to check whether earlier-period filter selection survives a different chronological period.

Baseline: mechanical CRT, fixed 1.5 nominal reward/risk, $50,000 initial account, 1% current-equity planned risk, same assumed commissions/slippage and whole-contract sizing as [the risk-sizing study](RISK_SIZING.md). Direct-break enters C3's first break of the relevant C2 extreme; rebreak additionally requires an observed (within the hypothetical path) break, pullback and second break.

**Result: {improved} of {selected_count} shortlisted filters improved the later-period win rate versus the corresponding baseline under both bar paths.** A modest full-year improvement for New York-only direct entries did not improve the later-period comparison. These tests do not validate a new win-rate filter for deployment.

Ten simple single filters were declared in [filter_protocol.json](research/filter_protocol.json). They concern session/time, 15-minute setup timeframe, C2 body direction, rejection strength, sweep depth, preceding price efficiency, minimum target distance, the existing hourly bias proxy, and Mondays. No filter combinations or threshold grid were searched. All filter features are available when C2 closes; no C3 high/low or future trade result is used to accept a setup.

Only trades entered before **1 July 2026** are used to shortlist two filters per entry method. Selection ranks the minimum Wilson win-rate lower endpoint across high-first/low-first assumptions, requiring at least 40 earlier-period baseline trades under each path. This first stage is subset screening, not a deployable policy backtest. The shortlisted filters are then **fully resimulated**: rejected setups may be replaced by the next qualified setup. The later results do not change which filters are selected.

## Full policy resimulations

Ranges compare the two assumed minute OHLC paths; they are not confidence bounds or rigorous best/worst bounds. Net-positive win rate includes modeled fees and slippage. Period return uses that period's starting equity; the later period continues the earlier account rather than starting a fresh one.

| Entry | Filter | Period | Trades | Net win rate | Mean net R | Compounded return |
|---|---|---|---:|---:|---:|---:|
{chr(10).join(lines)}

Win rate is one criterion. A filter can raise it while reducing trade count, expectancy, or return, and an apparent improvement on a small later sample is weak evidence. A Wilson lower endpoint used for selection is not a multiple-testing or dependence correction.

The original minute-path uncertainty remains substantial, particularly for direct entries. Margin, quantity limits, liquidity and market impact remain unmodeled, and old-contract data gaps use explicitly labeled last-mark rollover liquidation. These are conditional simulations. No new filter is automatically enabled in the paper/live configuration.

[Machine-readable results](research/results/filter_study_summary.json) contain every earlier-period candidate score, the fixed shortlist, and full selected-policy outcomes. Per-case trade journals are local in `runs/filter_study/`. Reproduce with `.\\.venv\\Scripts\\python.exe -m research.filter_study`. Protocol SHA-256: `{record['protocol_sha256']}`.

The need to account for repeated testing and data snooping is discussed in [CME's backtesting research](https://www.cmegroup.com/content/dam/cmegroup/education/files/backtesting.pdf). Selection and later-period results here remain exploratory.
'''
    Path("FILTER_RESEARCH.md").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
