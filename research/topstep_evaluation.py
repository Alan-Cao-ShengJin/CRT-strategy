"""Current-rule numerical evaluation study; no real accounts or orders are touched."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import date, datetime, time, timedelta
from math import ceil, floor
from pathlib import Path
from statistics import median

from crt.config import Config
from crt.data import read_bars
from crt.engine import Engine
from crt.models import INSTRUMENTS, NY, UTC
from crt.strategy import trading_day

PROTOCOL = Path("research/topstep_eval_protocol.json")


def file_hashes(paths):
    return {str(p): hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths}


class TopstepEvaluation(Engine):
    def __init__(self, config, rules, start_day):
        super().__init__(config)
        self.rules = rules
        self.start_day = start_day
        self.status = "warming_up"
        self.floor = config.initial_equity-rules["loss_allowance"]
        self.prop_day = None
        self.prop_day_start_cash = config.initial_equity
        self.day_entries = 0
        self.day_results = []
        self.best_day = 0.
        self.traded_days = 0
        self.terminal_time = None
        self.failure_equity = None
        self.failure_floor = None
        self.max_qty = 0
        self.achieved_risk = []
        self.ready_for_eod = False

    def activate(self, at):
        self.status = "active"
        self.cash = self.config.initial_equity
        self.peak = self.cash
        self.max_drawdown_seen = 0.
        self.day = ""
        self.day_start = self.cash
        self.halt_reason = ""
        self.prop_day = trading_day(at)
        self.prop_day_start_cash = self.cash
        for state in self.states.values():
            state.pending = None
            state.selected.clear()

    def deadline(self):
        return datetime.combine(date.fromisoformat(self.prop_day), time.fromisoformat(self.rules["day_finalization_time_NY"]), NY).astimezone(UTC)

    def censor(self, status, at):
        if self.status == "active":
            self.status, self.terminal_time = status, at
            for state in self.states.values():
                state.pending = None

    def finalize_day(self):
        if self.status != "active" or self.prop_day is None:
            return
        if self.day_results and self.day_results[-1]["day"] == self.prop_day:
            return
        at = self.deadline()
        if self.positions:
            self.censor("data_gap_censored", at)
            return
        pnl = self.cash-self.prop_day_start_cash
        self.best_day = max(self.best_day, pnl)
        self.traded_days += int(self.day_entries > 0)
        self.floor = min(self.config.initial_equity, max(self.floor, self.cash-self.rules["loss_allowance"]))
        required = max(self.rules["profit_target"], self.best_day/self.rules["consistency_fraction"])
        self.day_results.append({"day": self.prop_day, "pnl": pnl, "balance": self.cash,
                                 "floor_after_close": self.floor, "entries": self.day_entries,
                                 "required_total_profit": required})
        if self.cash-self.config.initial_equity >= required-1e-8 and self.traded_days >= 2:
            self.status, self.terminal_time = "passed", at
            for state in self.states.values():
                state.pending = None

    def _clock(self, at):
        if self.status == "active" and trading_day(at) != self.prop_day:
            self.finalize_day()
            if self.status == "active":
                self.prop_day = trading_day(at)
                self.prop_day_start_cash = self.cash
                self.day_entries = 0
                self.ready_for_eod = False
        super()._clock(at)

    def _exit(self, symbol, price, at, reason, market=False):
        super()._exit(symbol, price, at, reason, market=market)
        if self.status != "active" or self.positions:
            return
        best = max(self.best_day, self.cash-self.prop_day_start_cash)
        required = max(self.rules["profit_target"], best/self.rules["consistency_fraction"])
        days = self.traded_days + int(self.day_entries > 0)
        if self.cash-self.config.initial_equity >= required-1e-8 and days >= 2:
            self.ready_for_eod = True
            for state in self.states.values():
                state.pending = None
            self.log("evaluation_target_locked", at, balance=self.cash, required_profit=required)

    def _accept_setup(self, setup, c1, c2, chop):
        return not self.ready_for_eod

    def _risk_check(self, at):
        super()._risk_check(at)
        if self.status == "active" and self.equity <= self.floor+1e-8:
            self.status, self.terminal_time = "failed_mll", at
            self.failure_equity, self.failure_floor = self.equity, self.floor
        if self.status == "failed_mll":
            self.halt_reason = "prop_mll"
            self.permanent_halt = True
            for state in self.states.values():
                state.pending = None

    def _additional_position_levels(self, position):
        inst = INSTRUMENTS[position.setup.symbol]
        threshold = position.entry + position.setup.side*(self.floor-self.cash)/(position.qty*inst.point_value)
        level = (floor(threshold/inst.tick+1e-8) if position.setup.side == 1 else ceil(threshold/inst.tick-1e-8))*inst.tick
        return [round(level, 8)] if level > 0 else []

    def _enter(self, setup, price, at):
        if self.status != "active" or self.ready_for_eod:
            return False
        local = at.astimezone(NY)
        minute = local.hour*60+local.minute
        if (505 <= minute < 515) or (835 <= minute < 845) or (865 <= minute < 875):
            return False
        before = self.equity
        accepted = super()._enter(setup, price, at)
        if accepted:
            self.day_entries += 1
            p = self.positions[setup.symbol]
            self.max_qty = max(self.max_qty, p.qty)
            self.achieved_risk.append(p.risk/before)
        return accepted

    def consume(self, bar):
        if self.status == "warming_up":
            if trading_day(bar.start) < self.start_day:
                self.on_bar_batch([bar], learn_only=True)
                return
            self.activate(bar.start)
        if self.status != "active":
            return
        deadline = self.deadline()
        # Once time has crossed a required close, a missing exit price is not a fill.
        if bar.start > deadline and self.positions:
            self.censor("data_gap_censored", deadline)
            return
        self.on_bar_batch([bar])
        if self.status == "active" and bar.end >= self.deadline():
            self.finalize_day()


def evaluation_config(path, rules):
    cfg = Config(symbols=["MNQ"], entry_mode="rebreak", bar_model=path,
                 bias_enabled=False, chop_enabled=False, neutral_policy="first",
                 initial_equity=rules["initial_balance"], risk_fraction=rules["risk_fraction"],
                 risk_per_trade=1e12, max_open_risk=1e12, max_daily_loss=1e12,
                 max_drawdown=1e12, max_contracts=rules["max_micros"], max_positions=1,
                 min_target_ticks=1, min_stop_ticks=1, flatten_time=rules["flatten_time_NY"],
                 schedule_file="data/annual/schedules.json")
    cfg.commission_per_side["MNQ"] = rules["commission_per_side"]
    cfg.slippage_ticks = rules["slippage_ticks"]
    return cfg.validate()


def run_attempt(bars, rules, path, start_day):
    cfg = evaluation_config(path, rules)
    e = TopstepEvaluation(cfg, rules, start_day)
    start = date.fromisoformat(start_day)
    warmup = (start-timedelta(days=7)).isoformat()
    horizon = start+timedelta(days=rules["horizon_calendar_days"])
    for b in bars:
        day = trading_day(b.start)
        if day < warmup:
            continue
        if day >= horizon.isoformat():
            e.censor("horizon_unfinished", datetime.combine(horizon, time(0), NY).astimezone(UTC))
            break
        e.consume(b)
        if e.status not in ("active", "warming_up"):
            break
    if e.status == "active":
        if e.last_time and e.last_time >= e.deadline():
            e.finalize_day()
        if e.status == "active":
            e.censor("data_end_censored", e.last_time)
    end_day = date.fromisoformat(trading_day(e.terminal_time)) if e.terminal_time else start
    elapsed_days = rules["horizon_calendar_days"] if e.status == "horizon_unfinished" else (end_day-start).days+1
    incomplete_traded_day = int(e.day_entries > 0 and (not e.day_results or e.day_results[-1]["day"] != e.prop_day))
    return {"start_trading_date": start_day, "bar_path": path, "status": e.status,
            "terminal_time_utc": e.terminal_time.isoformat() if e.terminal_time else None,
            "calendar_days_inclusive": elapsed_days,
            "completed_market_sessions": len(e.day_results), "days_with_trades": e.traded_days+incomplete_traded_day,
            "trades": len(e.trades), "balance_or_last_marked_equity": e.equity,
            "net_pnl_at_terminal": e.equity-cfg.initial_equity,
            "last_mll": e.floor, "failure_equity_at_touch": e.failure_equity,
            "failure_floor_at_touch": e.failure_floor, "best_day": e.best_day,
            "consistency_adjusted_target": max(rules["profit_target"], e.best_day/rules["consistency_fraction"]),
            "max_contracts_used": e.max_qty,
            "mean_achieved_risk_pct": sum(e.achieved_risk)/len(e.achieved_risk)*100 if e.achieved_risk else None,
            "max_achieved_risk_pct": max(e.achieved_risk)*100 if e.achieved_risk else None,
            "daily_results": e.day_results, "config": cfg.to_dict(),
            "trades_detail": [t.__dict__ for t in e.trades]}


def main(path=None):
    rules = json.loads(PROTOCOL.read_text())
    manifest = json.loads(Path("data/annual/manifest.json").read_text())
    inputs = [r["file"] for r in manifest if r["root"] == "MNQ"]
    bars = read_bars(inputs)
    available = sorted({trading_day(b.start) for b in bars})
    starts = [min(day for day in available if day.startswith(month)) for month in rules["cohort_start_months"]]
    record = {"protocol": rules, "protocol_sha256": hashlib.sha256(PROTOCOL.read_bytes()).hexdigest(),
              "input_sha256": file_hashes(inputs+["data/annual/manifest.json", "data/annual/schedules.json"]),
              "source_sha256": file_hashes(sorted(Path("crt").glob("*.py"))+[Path(__file__)]),
              "attempts": []}
    destination = Path(f"runs/topstep_evaluation/{path}.json") if path else Path("research/results/topstep_evaluation_summary.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    for p in ([path] if path else rules["paths"]):
        for start in starts:
            attempt = run_attempt(bars, rules, p, start)
            record["attempts"].append(attempt)
            destination.write_text(json.dumps(record, indent=2, allow_nan=False), encoding="utf-8")
            print(json.dumps({k: attempt[k] for k in ("start_trading_date", "bar_path", "status", "calendar_days_inclusive", "trades", "net_pnl_at_terminal")}), flush=True)
    if not path:
        write_note(record)


def merge(parts):
    docs = [json.loads(Path(p).read_text()) for p in parts]
    if len({d["protocol_sha256"] for d in docs}) != 1:
        raise ValueError("Protocol differs across partial runs")
    for key in ("input_sha256", "source_sha256"):
        if any(d[key] != docs[0][key] for d in docs):
            raise ValueError(f"{key} differs across partial runs")
    if docs[0]["protocol_sha256"] != hashlib.sha256(PROTOCOL.read_bytes()).hexdigest():
        raise ValueError("Current protocol differs from the simulated protocol")
    record = docs[0]
    attempts = [a for d in docs for a in d["attempts"]]
    if len(attempts) != 24 or len({(a["start_trading_date"], a["bar_path"]) for a in attempts}) != 24:
        raise ValueError("Expected 24 distinct attempts")
    record["attempts"] = attempts
    Path("research/results/topstep_evaluation_summary.json").write_text(json.dumps(record, indent=2, allow_nan=False), encoding="utf-8")
    write_note(record)


def write_note(record):
    rows = record["attempts"]
    lines, aggregates = [], []
    for path in record["protocol"]["paths"]:
        cases = [a for a in rows if a["bar_path"] == path]
        statuses = Counter(a["status"] for a in cases)
        passed = [a for a in cases if a["status"] == "passed"]
        failed = [a for a in cases if a["status"] == "failed_mll"]
        aggregates.append({"bar_path": path, "attempts": len(cases), "statuses": dict(statuses),
                           "pass_calendar_days": [a["calendar_days_inclusive"] for a in passed],
                           "pass_market_sessions": [a["completed_market_sessions"] for a in passed],
                           "pass_traded_days": [a["days_with_trades"] for a in passed],
                           "pass_closed_trades": [a["trades"] for a in passed],
                           "failure_calendar_days": [a["calendar_days_inclusive"] for a in failed],
                           "median_pass_calendar_days": median(a["calendar_days_inclusive"] for a in passed) if passed else None,
                           "median_failure_calendar_days": median(a["calendar_days_inclusive"] for a in failed) if failed else None})
        for a in cases:
            lines.append(f"| {a['start_trading_date']} | {path} | {a['status']} | {a['calendar_days_inclusive']} | {a['trades']} | ${a['net_pnl_at_terminal']:,.2f} |")
    record["aggregates"] = aggregates
    Path("research/results/topstep_evaluation_summary.json").write_text(json.dumps(record, indent=2, allow_nan=False), encoding="utf-8")
    summaries = ["| Assumed price order | Passed | Failed loss limit | Unresolved | Passing calendar days | Median among passes |",
                 "|---|---:|---:|---:|---|---:|"]
    for a in aggregates:
        statuses = a["statuses"]
        unresolved = a["attempts"]-statuses.get("passed", 0)-statuses.get("failed_mll", 0)
        summaries.append(f"| {a['bar_path']} | {statuses.get('passed', 0)}/{a['attempts']} | {statuses.get('failed_mll', 0)}/{a['attempts']} | {unresolved}/{a['attempts']} | {', '.join(map(str, a['pass_calendar_days']))} | {a['median_pass_calendar_days']} days |")
    text = f'''# MNQ rebreak: Topstep 150K evaluation timing at 1% risk

Current-rule historical simulation checked 5 November 2026. This tests the **standard $150,000 Trading Combine without the optional Daily Loss Limit add-on**, on MNQ mechanical rebreak only. It does not purchase, connect to, or trade a real evaluation.

## Results

{chr(10).join(summaries)}

These are overlapping historical cohorts, not independent probability trials. Passing times are **conditional on the attempts that passed**; failures and censored attempts have no passing time. The 90-calendar-day research horizon is not a firm deadline. Do not interpret the median among winners as an expected time until success.

One percent of nominal account equity initially risks $1,500, which is one-third of the $4,500 initial loss allowance. Successful historical windows therefore do not establish a dependable passing schedule. Calendar days below include the starting date, weekends and holidays. The JSON also records completed market sessions and days with trades.

| Start trading date | Assumed OHLC path | Outcome | Calendar days inclusive | Closed trades | P&L / last marked P&L |
|---|---|---|---:|---:|---:|
{chr(10).join(lines)}

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

Protocol: [topstep_eval_protocol.json](research/topstep_eval_protocol.json). Full daily balances, loss floors, trade journals, input hashes and source hashes: [topstep_evaluation_summary.json](research/results/topstep_evaluation_summary.json). Reproduce with `.\\.venv\\Scripts\\python.exe -m research.topstep_evaluation`.
'''
    Path("TOPSTEP_EVALUATION.md").write_text(text, encoding="utf-8")
    print(json.dumps(aggregates, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", choices=["high_first", "low_first"])
    parser.add_argument("--merge", nargs="+")
    args = parser.parse_args()
    if args.merge:
        merge(args.merge)
    else:
        main(args.path)
