"""Frozen-rule study: mechanical CRT vs disclosed discretionary proxies, with path sensitivity."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from crt.cli import run_backtest
from crt.config import Config
from crt.data import read_bars, coverage
from crt.models import timestamp
from crt.report import write_report
from crt.strategy import trading_day

PROTOCOL = Path("research/protocol.json")


def session_days(start, end):
    begin, finish = date.fromisoformat(start), date.fromisoformat(end)
    return [(begin+timedelta(days=i)).isoformat() for i in range((finish-begin).days)
            if (begin+timedelta(days=i)).weekday() < 5]


def metrics(trades, days, protocol):
    by_day = defaultdict(float)
    by_month = defaultdict(float)
    for t in trades:
        day = trading_day(timestamp(t.entry_time))
        by_day[day] += t.net
        by_month[day[:7]] += t.net
    pnl = np.array([by_day[d] for d in days], dtype=float)
    rng = np.random.default_rng(protocol["bootstrap_seed"])
    if len(pnl):
        samples = rng.choice(pnl, size=(protocol["daily_block_bootstrap_repetitions"], len(pnl)), replace=True).sum(axis=1)
        ci = np.quantile(samples, [.025, .975]).tolist()
    else:
        ci = [0., 0.]
    wins = sum(t.net > 0 for t in trades)
    n = len(trades)
    if n:
        z = 1.959963984540054
        p = wins/n
        middle = (p+z*z/(2*n))/(1+z*z/n)
        half = z*((p*(1-p)+z*z/(4*n))/n)**.5/(1+z*z/n)
        wilson = [middle-half, middle+half]
    else:
        wilson = [None, None]
    total_qty = sum(t.qty for t in trades)
    gross = sum(t.gross for t in trades)
    net = sum(t.net for t in trades)
    gains = sum(max(t.net, 0) for t in trades)
    loss = -sum(min(t.net, 0) for t in trades)
    stressed = net - 2*total_qty*(protocol["cost_stress_commission_per_side_usd"] - protocol["commission_per_side_per_contract_usd"])
    return {"trades": n, "wins": wins, "win_rate": wins/n if n else None,
            "target_hits": sum(t.reason == "target" for t in trades),
            "target_hit_rate": sum(t.reason == "target" for t in trades)/n if n else None,
            "wilson_win_rate_95": wilson, "gross_pnl_after_slippage": gross,
            "fees": sum(t.fees for t in trades), "net_pnl": net,
            "net_per_trade": net/n if n else None,
            "profit_factor": gains/loss if loss else None,
            "sum_net_r": sum(t.r_multiple for t in trades),
            "net_r_per_trade": sum(t.r_multiple for t in trades)/n if n else None,
            "daily_block_bootstrap_95_net_pnl": ci,
            "cost_stress_net_pnl": stressed,
            "commission_break_even_per_side": gross/(2*total_qty) if total_qty else None,
            "active_days": len(by_day), "calendar_weekdays_including_holidays": len(days),
            "monthly_net_pnl": dict(sorted(by_month.items())),
            "days": dict(sorted(by_day.items()))}


def study(input_dir, output_dir, summary_file):
    protocol = json.loads(PROTOCOL.read_text())
    protocol_hash = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()
    input_dir, output_dir = Path(input_dir), Path(output_dir)
    manifest = json.loads((input_dir / "manifest.json").read_text())
    results = []
    record = {"protocol": protocol, "protocol_sha256": protocol_hash, "results": results, "coverage": {}, "input_files": [], "path_sensitivity": []}
    trade_sets = {}
    for symbol in protocol["symbols"]:
        inputs = [r for r in manifest if r["root"] == symbol]
        for row in inputs:
            source = Path(row["file"])
            record["input_files"].append({**row, "bytes": source.stat().st_size, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()})
        bars = read_bars([r["file"] for r in inputs])
        record["coverage"].update(coverage(bars))
        for filters in protocol["filters"]:
            for mode in protocol["entry_modes"]:
                for path in protocol["bar_paths"]:
                    name = f"{symbol}_{filters}_{mode}_{path}"
                    cfg = Config(symbols=[symbol], entry_mode=mode, bar_model=path,
                                 bias_enabled=filters != "mechanical", chop_enabled=filters != "mechanical",
                                 neutral_policy="first" if filters == "mechanical" else "skip",
                                 min_target_ticks=1, min_stop_ticks=1, max_positions=1,
                                 risk_fraction=1., max_open_risk=100., max_drawdown=1e12,
                                 schedule_file=str(input_dir / "schedules.json"))
                    engine = run_backtest(cfg, bars, protocol["evaluation_start_trading_date"], protocol["evaluation_end_exclusive_trading_date"])
                    stats = metrics(engine.trades, session_days(protocol["evaluation_start_trading_date"], protocol["evaluation_end_exclusive_trading_date"]), protocol)
                    stats.update({"symbol": symbol, "filters": filters, "entry_mode": mode, "bar_path": path,
                                  "max_marked_drawdown": engine.summary()["max_drawdown"],
                                  "report": str(output_dir / name / "report.html"), "config": cfg.to_dict()})
                    split = protocol["chronological_split"]
                    stats["chronological_early"] = metrics([t for t in engine.trades if trading_day(timestamp(t.entry_time)) < split],
                                                          session_days(protocol["evaluation_start_trading_date"], split), protocol)
                    stats["chronological_late"] = metrics([t for t in engine.trades if trading_day(timestamp(t.entry_time)) >= split],
                                                         session_days(split, protocol["evaluation_end_exclusive_trading_date"]), protocol)
                    stats["reasons"] = {reason: sum(t.reason == reason for t in engine.trades) for reason in sorted({t.reason for t in engine.trades})}
                    trade_sets[(symbol, filters, mode, path)] = {t.setup_id: t for t in engine.trades}
                    if path == "low_first":
                        high = trade_sets[(symbol, filters, mode, "high_first")]
                        low = trade_sets[(symbol, filters, mode, "low_first")]
                        common = set(high) & set(low)
                        record["path_sensitivity"].append({"symbol": symbol, "filters": filters, "entry_mode": mode,
                                                           "common_setups_traded": len(common),
                                                           "only_high_first_traded": len(set(high)-set(low)),
                                                           "only_low_first_traded": len(set(low)-set(high)),
                                                           "different_exit_reason": sum(high[k].reason != low[k].reason for k in common),
                                                           "different_profit_sign": sum((high[k].net > 0) != (low[k].net > 0) for k in common)})
                    # Keep the report usable; risk drawdown is tracked at every processed event.
                    engine.curve = engine.curve[::60] + engine.curve[-1:]
                    write_report(engine, output_dir / name, {"study": name, "protocol_sha256": protocol_hash,
                                                           "independent_instrument_account": True, "curve_sampling": "every 60 observed minutes"})
                    results.append(stats)
                    destination = Path(summary_file)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_text(json.dumps(record, indent=2, allow_nan=False), encoding="utf-8")
                    print(json.dumps({k: stats[k] for k in ("symbol", "filters", "entry_mode", "bar_path", "trades", "win_rate", "net_pnl", "profit_factor")}), flush=True)
        del bars
    render_comparison(record, output_dir)
    return record


def render_comparison(record, output_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = record["results"]
    fig, axes = plt.subplots(1, 3, figsize=(15, 5), sharey=True, layout="constrained")
    labels = ["Core / direct", "Core / rebreak", "Proxy / direct", "Proxy / rebreak"]
    for ax, symbol in zip(axes, record["protocol"]["symbols"]):
        for path, offset, color in (("high_first", -.18, "#287bba"), ("low_first", .18, "#dd8741")):
            values = [r["net_pnl"] for r in rows if r["symbol"] == symbol and r["bar_path"] == path]
            ax.bar(np.arange(4)+offset, values, width=.36, color=color, label=path.replace("_", " "))
        ax.axhline(0, color="#333", linewidth=.8)
        ax.set_title(symbol)
        ax.set_xticks(np.arange(4), labels, rotation=30, ha="right")
        ax.grid(axis="y", alpha=.2)
    axes[0].set_ylabel("Net USD after assumed costs / independent $50k accounts")
    axes[-1].legend(fontsize=9)
    fig.suptitle("CRT: one-year results under two assumed minute-bar paths\n3 Nov 2025 – 4 Nov 2026 · no parameter fitting · paths are not observed ticks", fontsize=13)
    output_dir = Path(output_dir)
    fig.savefig(output_dir / "comparison.png", dpi=170)
    plt.close(fig)
    import html
    body = "".join("<tr>"+"".join(f"<td>{html.escape(str(r[k]) if k in ('symbol','filters','entry_mode','bar_path','trades') else format(r[k], '.2f') if r[k] is not None else 'N/A')}</td>" for k in ("symbol","filters","entry_mode","bar_path","trades","win_rate","net_pnl","profit_factor","cost_stress_net_pnl"))+"</tr>" for r in rows)
    page = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>CRT transcript audit</title>
<style>body{{font:16px system-ui;margin:clamp(15px,4vw,50px);color:#182b40;line-height:1.6}}img{{width:100%;max-width:1500px}}.scroll{{overflow:auto}}table{{border-collapse:collapse}}th,td{{padding:10px;border-bottom:1px solid #bbc6d0;text-align:left;white-space:nowrap}}p{{max-width:100ch}}</style>
<h1>Does the transcript's CRT strategy make money?</h1><p>This study tests fixed mechanical rules and a separate approximation of discretionary hourly bias/chop, on dated MNQ, MES and MGC minute bars. Each instrument has its own simulated account. High-first and low-first paths are hypothetical OHLC orderings; they are not tick reconstructions or rigorous bounds. Win rate in the table is a fraction.</p>
<p>Period: 3 November 2025 through 4 November 2026, with warmup. Nominal risk: at most $100 per trade, whole contracts, maximum two daily entries. Reward/risk: 1.5 before costs. Fees: assumed $0.85 per side per contract; one adverse tick at entry and stops. Stress case reprices the same fills at $1.50 per side without resimulating sizing. Automated bias/chop thresholds are assumptions; the trader's complete discretionary record is unavailable.</p>
<img src="comparison.png" alt="Net P and L by instrument, rule set and assumed bar path"><h2>All predeclared cases</h2><div class="scroll"><table><thead><tr><th>Symbol</th><th>Filters</th><th>Entry</th><th>Path</th><th>Trades</th><th>Win rate</th><th>Net USD</th><th>Profit factor</th><th>Stress net USD</th></tr></thead><tbody>{body}</tbody></table></div>
<p>Full trades and account reports are in each case directory. Reproducible protocol, monthly results, confidence intervals, chronology splits and coverage are stored in research/results/annual_summary.json. The daily bootstrap treats weekdays as independent blocks and includes zero-trade dates; it does not adjust for serial regime dependence, multiple comparisons or discretionary-model uncertainty. No real-money orders were sent.</p></html>'''
    (output_dir / "report.html").write_text(page, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/annual")
    parser.add_argument("--output", default="runs/annual_audit")
    parser.add_argument("--summary", default="research/results/annual_summary.json")
    args = parser.parse_args()
    study(args.input, args.output, args.summary)
