"""Rerun the annual cases at the user's requested equity fractions, with compounding."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from crt.config import Config
from crt.data import batches, read_bars
from crt.engine import Engine
from crt.report import write_report
from crt.strategy import trading_day


class EquityRiskEngine(Engine):
    def __init__(self, config):
        super().__init__(config)
        self.max_fractional_drawdown = 0.
        self.entry_risk_fractions = []
        self.entry_quantities = []
        self.roll_data_liquidations = []

    def _risk_check(self, at):
        super()._risk_check(at)
        if self.peak > 0:
            self.max_fractional_drawdown = max(self.max_fractional_drawdown, (self.peak-self.equity)/self.peak)

    def _enter(self, setup, price, at):
        before = self.equity
        accepted = super()._enter(setup, price, at)
        if accepted:
            position = self.positions[setup.symbol]
            self.entry_risk_fractions.append(position.risk/before)
            self.entry_quantities.append(position.qty)
        return accepted


def fractional_config(base, fraction):
    """Make equity fraction binding instead of the former fixed-dollar/quantity caps."""
    values = dict(base)
    values.update(risk_fraction=fraction, risk_per_trade=1e12, max_open_risk=1e12,
                  max_daily_loss=1e12, max_drawdown=1e12, max_contracts=1_000_000)
    return Config(**values).validate()


def run_case(cfg, bars, start, end):
    engine = EquityRiskEngine(cfg)
    for batch in batches(bars):
        day = trading_day(batch[0].start)
        if day >= end:
            break
        for bar in batch:
            state = engine.states[bar.symbol]
            if state.contract is not None and state.contract != bar.contract and bar.symbol in engine.positions:
                # The cached contract window has ended. Keep this distinct from an
                # observed fill: missing old-contract prices cannot be reconstructed.
                last = state.last_bar
                engine._exit(bar.symbol, engine.marks[bar.symbol], last.end, "roll_data_end_mark", market=True)
                trade = engine.trades[-1]
                engine.roll_data_liquidations.append({"contract": trade.contract, "time": trade.exit_time,
                                                      "net": trade.net, "reason": trade.reason,
                                                      "next_contract_first_bar": bar.start.isoformat()})
                engine._risk_check(last.end)
                engine.curve.append({"time": last.end.isoformat(), "equity": engine.equity})
        engine.on_bar_batch(batch, learn_only=day < start)
    engine.finish()
    engine._risk_check(engine.last_time)
    return engine


def study(symbols=None, summary_file="research/results/risk_sizing_summary.json"):
    base_file = Path("research/results/annual_summary.json")
    annual = json.loads(base_file.read_text())
    manifest = json.loads(Path("data/annual/manifest.json").read_text())
    protocol = annual["protocol"]
    record = {
        "period_start": protocol["evaluation_start_trading_date"],
        "period_end_exclusive": protocol["evaluation_end_exclusive_trading_date"],
        "initial_equity": 50000., "risk_fractions": [.01, .02],
        "source_annual_summary_sha256": hashlib.sha256(base_file.read_bytes()).hexdigest(),
        "method": "Full resimulation with current-equity sizing, compounding, whole contracts and unchanged signals/costs.",
        "limitations": [
            "Independent instrument accounts; percentages cannot be added into a shared portfolio return.",
            "The fixed $100 risk, $300 daily loss and ten-contract caps are removed so 1%/2% sizing can bind.",
            "Two trades per trading day and one simultaneous position remain; permanent drawdown cutoff stays disabled.",
            "Margin requirements, liquidity, market impact and account/broker contract limits are not modeled.",
            "All order paths are assumed minute-bar paths, not observed ticks. Gaps may exceed planned risk.",
            "Larger risk budgets may admit trades that the original $100 budget could not size.",
            "If old-contract data ends before a scheduled exit, rollover bookkeeping liquidates at its last observed mark plus adverse slippage; these are not observed fills."
        ], "results": []}
    output = Path("runs/risk_sizing")
    for symbol in (symbols or protocol["symbols"]):
        bars = read_bars([r["file"] for r in manifest if r["root"] == symbol])
        for base in [r for r in annual["results"] if r["symbol"] == symbol]:
            for fraction in record["risk_fractions"]:
                cfg = fractional_config(base["config"], fraction)
                name = f"{symbol}_{base['filters']}_{base['entry_mode']}_{base['bar_path']}_{int(fraction*100)}pct"
                engine = run_case(cfg, bars, record["period_start"], record["period_end_exclusive"])
                summary = engine.summary()
                risks = engine.entry_risk_fractions
                row = {"symbol": symbol, "filters": base["filters"], "entry_mode": base["entry_mode"],
                       "bar_path": base["bar_path"], "risk_fraction": fraction, **summary,
                       "total_return_pct": (engine.equity/cfg.initial_equity-1)*100,
                       "max_drawdown_pct": engine.max_fractional_drawdown*100,
                       "mean_planned_risk_pct": sum(risks)/len(risks)*100 if risks else None,
                       "max_planned_risk_pct": max(risks)*100 if risks else None,
                       "max_contracts_used": max(engine.entry_quantities, default=0),
                       "roll_data_liquidations": engine.roll_data_liquidations,
                       "baseline_fixed_100_return_pct": base["net_pnl"]/cfg.initial_equity*100,
                       "config": cfg.to_dict(), "report": str(output/name/"report.html")}
                engine.curve = engine.curve[::60]+engine.curve[-1:]
                write_report(engine, output/name, {"risk_sizing_study": True, "total_return_pct": row["total_return_pct"],
                                                  "max_drawdown_pct": row["max_drawdown_pct"], "margin_modeled": False})
                record["results"].append(row)
                destination = Path(summary_file)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(json.dumps(record, indent=2, allow_nan=False), encoding="utf-8")
                print(json.dumps({k: row[k] for k in ("symbol", "filters", "entry_mode", "bar_path", "risk_fraction", "trades", "total_return_pct", "max_drawdown_pct", "max_contracts_used")}), flush=True)
        del bars
    if len(record["results"]) == 48:
        write_note(record)
    return record


def merge(parts):
    documents = [json.loads(Path(p).read_text()) for p in parts]
    if len({d["source_annual_summary_sha256"] for d in documents}) != 1:
        raise ValueError("Partial runs have different source annual summaries")
    record = documents[0]
    rows = [r for d in documents for r in d["results"]]
    identities = {(r["symbol"], r["filters"], r["entry_mode"], r["bar_path"], r["risk_fraction"]) for r in rows}
    if len(rows) != 48 or len(identities) != 48:
        raise ValueError("Expected 48 distinct completed cases")
    record["results"] = rows
    Path("research/results/risk_sizing_summary.json").write_text(json.dumps(record, indent=2, allow_nan=False), encoding="utf-8")
    write_note(record)
    return record


def interval(values, signed=True):
    lo, hi = min(values), max(values)
    fmt = "+.2f" if signed else ".2f"
    return f"{format(lo, fmt)}% to {format(hi, fmt)}%" if abs(hi-lo) > .00001 else f"{format(lo, fmt)}%"


def write_note(record):
    rows = record["results"]
    lines = []
    for symbol in ("MNQ", "MES", "MGC"):
        for filters in ("mechanical", "hourly_proxy_and_chop"):
            for mode in ("direct", "rebreak"):
                values = [r for r in rows if r["symbol"] == symbol and r["filters"] == filters and r["entry_mode"] == mode]
                one = [r for r in values if r["risk_fraction"] == .01]
                two = [r for r in values if r["risk_fraction"] == .02]
                lines.append(f"| {symbol} | {'Mechanical' if filters == 'mechanical' else 'Bias/chop proxy'} | {mode} | {interval([r['baseline_fixed_100_return_pct'] for r in one])} | {interval([r['total_return_pct'] for r in one])} | {interval([r['total_return_pct'] for r in two])} | {interval([r['max_drawdown_pct'] for r in one], False)} | {interval([r['max_drawdown_pct'] for r in two], False)} |")
    note = f'''# CRT returns at 1% and 2% account risk

Same data and signals as [the annual audit](ANNUAL_BACKTEST.md): 3 November 2025 through 4 November 2026. Each instrument/variant has an independent **$50,000 starting account**. Positions target **1% or 2% of current marked equity per trade**, including modeled round-trip fees and one adverse stop tick in the planned risk budget. Whole-contract rounding normally uses slightly less. Gains and losses compound; these are full reruns, not a linear multiplication of the $100-risk result.

Total return = (ending equity / $50,000 − 1) × 100. Drawdown is the maximum percentage decline from the running equity peak along each assumed OHLC path. The two numbers in each range are the two bar-order assumptions, not confidence bounds or guaranteed extrema.

| Instrument | Filters | Entry | Original fixed $100 return | Return at 1% risk | Return at 2% risk | Max drawdown at 1% | Max drawdown at 2% |
|---|---|---|---:|---:|---:|---:|---:|
{chr(10).join(lines)}

The fixed $100 trade cap, $300 daily loss cap and ten-contract cap had to be removed for full percentage sizing. The two-trade daily limit, one-position limit, session rules, 1.5 nominal reward/risk and original assumed costs remain. As in the annual audit, there is no permanent account drawdown cutoff. Brokerage/exchange margin, available liquidity, market impact and broker quantity limits are **not modeled**. Tight stops can therefore produce unusually large micro-contract quantities; these are sizing simulations, not demonstrated executable returns. The maximum observed quantity across the runs was **{max(r['max_contracts_used'] for r in rows):,} contracts**. Actual reported mean/max planned risk fractions and quantities are in the JSON.

The larger risk budgets can also permit trades whose minimum whole-contract risk exceeded the old $100 limit. That can change trade counts and performance. Fees remain an assumed $0.85 per side per contract, with one adverse tick at entry and stop/market exits. Gaps can exceed planned risk. Results are alternative independent accounts; they must not be summed to claim one shared-account portfolio return.

Data-end assumption: some newly eligible positions span a gap at a dated-contract transition. The data source returned no prices for the missing old-contract interval. Those positions are marked out at the last observed old-contract price plus adverse slippage, explicitly labeled `roll_data_end_mark`; these are not verified executable fills. Such bookkeeping occurred in **{sum(bool(r['roll_data_liquidations']) for r in rows)} of {len(rows)} cases**. Each affected contract, timestamp and P&L contribution is retained in the JSON. Returns and drawdowns are conditional on this additional data-gap assumption.

Minute-bar path and discretionary-model limitations remain unchanged. Mechanical cases use explicit CRT rules; proxy cases add the existing approximation of hourly bias and chop. Increasing risk does not establish an edge that was absent or uncertain in the underlying tests.

The meaning of account-equity risk and its relation to stop distance/quantity follows [CME's position-sizing explanation](https://www.cmegroup.com/education/courses/trade-and-risk-management/proper-position-size). [Margin and position-risk requirements are separate considerations](https://www.cmegroup.com/education/courses/things-to-know-before-trading-cme-futures/position-and-risk-management).

Reproduce:

```powershell
.\\.venv\\Scripts\\python.exe -m research.risk_sizing_study
```

[Machine-readable results](research/results/risk_sizing_summary.json) contain all 48 cases, trade counts, final equity, returns, drawdowns, achieved risk fractions and full configurations. Detailed trade reports remain in `runs/risk_sizing/`.
'''
    Path("RISK_SIZING.md").write_text(note, encoding="utf-8")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", choices=["MNQ", "MES", "MGC"])
    parser.add_argument("--summary", default="research/results/risk_sizing_summary.json")
    parser.add_argument("--merge", nargs="+")
    args = parser.parse_args()
    if args.merge:
        merge(args.merge)
    else:
        study(args.symbols, args.summary)
