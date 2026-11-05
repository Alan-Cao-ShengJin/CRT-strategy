from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .config import Config
from .data import read_bars, write_bars, read_ticks, batches, coverage
from .engine import Engine
from .massive import MassiveClient, MassiveError, contract_root
from .report import write_report


def parser():
    p = argparse.ArgumentParser(prog="crt", description="CRT futures research and paper trading (no broker execution)")
    commands = p.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Write a default configuration")
    init.add_argument("--output", default="config.json")
    contracts = commands.add_parser("contracts", help="Discover dated Massive futures contracts")
    contracts.add_argument("--symbols", nargs="+", default=["MNQ", "MES", "MGC"])
    contracts.add_argument("--date", default=date.today().isoformat())
    contracts.add_argument("--env-file", default=".env")
    download = commands.add_parser("download", help="Download dated contracts to normalized minute CSV")
    download.add_argument("--contracts", nargs="+", required=True)
    download.add_argument("--start", required=True, help="Inclusive UTC YYYY-MM-DD")
    download.add_argument("--end", required=True, help="Exclusive UTC YYYY-MM-DD")
    download.add_argument("--output", default="data/history.csv")
    download.add_argument("--env-file", default=".env")
    schedules = commands.add_parser("schedules", help="Download exchange open/close events for holidays and early closes")
    schedules.add_argument("--symbols", nargs="+", default=["MNQ", "MES", "MGC"])
    schedules.add_argument("--start", required=True)
    schedules.add_argument("--end", required=True)
    schedules.add_argument("--output", default="data/schedules.json")
    schedules.add_argument("--env-file", default=".env")
    backtest = commands.add_parser("backtest", help="Run a portfolio backtest")
    backtest.add_argument("--data", nargs="+", required=True)
    backtest.add_argument("--config")
    backtest.add_argument("--schedule-file")
    backtest.add_argument("--output", default="runs/backtest")
    backtest.add_argument("--start", help="First trading date, YYYY-MM-DD; earlier bars warm up indicators")
    backtest.add_argument("--end", help="Exclusive trading date, YYYY-MM-DD")
    backtest.add_argument("--ticks", action="store_true", help="Read tick JSONL instead of minute CSV")
    compare = commands.add_parser("compare", help="Fixed-rule forward windows and direct/rebreak comparison")
    compare.add_argument("--data", nargs="+", required=True)
    compare.add_argument("--config")
    compare.add_argument("--schedule-file")
    compare.add_argument("--start", required=True)
    compare.add_argument("--end", required=True)
    compare.add_argument("--window-days", type=int, default=30)
    compare.add_argument("--output", default="runs/comparison")
    for name in ("paper", "probe"):
        paper = commands.add_parser(name, help="Massive streaming paper account" if name == "paper" else "Check Massive WebSocket auth and trade entitlements")
        paper.add_argument("--contracts", nargs="+", required=True)
        paper.add_argument("--config")
        paper.add_argument("--schedule-file")
        paper.add_argument("--journal", default=f"runs/{name}.sqlite")
        paper.add_argument("--output", default=f"runs/{name}")
        paper.add_argument("--feed", choices=["realtime", "delayed"], default="realtime")
        paper.add_argument("--transport", choices=["rest", "websocket"], default="rest" if name == "paper" else "websocket")
        paper.add_argument("--delay-seconds", type=int, default=0, help="Explicit REST simulation delay; 28800 for an eight-hour historical feed")
        paper.add_argument("--poll-seconds", type=float, default=60)
        paper.add_argument("--duration", type=float, default=20 if name == "probe" else 0)
        paper.add_argument("--env-file", default=".env")
        paper.add_argument("--kill-file", default="STOP")
        paper.add_argument("--warmup-days", type=int, default=5)
    return p


def run_backtest(config, bars, start=None, end=None):
    from .strategy import trading_day
    engine = Engine(config)
    for batch in batches(bars):
        day = trading_day(batch[0].start)
        if end and day >= end:
            break
        engine.on_bar_batch(batch, learn_only=bool(start and day < start))
    engine.finish()
    return engine


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "init":
            path = Path(args.output)
            if path.exists():
                raise ValueError(f"{path} already exists")
            path.write_text(json.dumps(Config().to_dict(), indent=2), encoding="utf-8")
            print(f"Created {path}")
        elif args.command == "contracts":
            client = MassiveClient(env_file=args.env_file)
            date.fromisoformat(args.date)
            for root in args.symbols:
                records = sorted(client.contracts(root, args.date), key=lambda r: r.get("last_trade_date", "9999"))
                print(json.dumps({"symbol": root, "as_of": args.date, "contracts": records}, indent=2))
        elif args.command == "download":
            begin, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
            if end <= begin:
                raise ValueError("end must follow start")
            roots = [contract_root(t) for t in args.contracts]
            if len(set(roots)) != len(roots):
                raise ValueError("Download one contract per root per file; split rollover periods")
            client, bars = MassiveClient(env_file=args.env_file), []
            for ticker in args.contracts:
                rows = client.download(ticker, args.start, args.end)
                print(f"{ticker}: {len(rows)} minute bars", flush=True)
                if not rows:
                    raise MassiveError(f"No bars for {ticker}; check contract dates and entitlements")
                bars.extend(rows)
            bars.sort(key=lambda b: (b.start, b.symbol))
            write_bars(args.output, bars)
            print(json.dumps(coverage(bars), indent=2))
        elif args.command == "schedules":
            if date.fromisoformat(args.end) < date.fromisoformat(args.start):
                raise ValueError("end must not precede start")
            client, records = MassiveClient(env_file=args.env_file), []
            for symbol in args.symbols:
                records.extend(client.schedules(symbol, args.start, args.end))
            if not records:
                raise MassiveError("No exchange schedules returned")
            path = Path(args.output)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(records, indent=2), encoding="utf-8")
            print(f"Saved {len(records)} schedule events to {path}")
        elif args.command == "backtest":
            config = Config.load(args.config)
            if args.schedule_file:
                config.schedule_file = args.schedule_file
            if args.start:
                date.fromisoformat(args.start)
            if args.end:
                date.fromisoformat(args.end)
            if args.start and args.end and args.end <= args.start:
                raise ValueError("end must follow start")
            if args.ticks:
                if args.start or args.end:
                    raise ValueError("Tick files should contain the desired replay interval; date slicing is for bars")
                engine = Engine(config)
                for tick in sorted((t for path in args.data for t in read_ticks(path)), key=lambda t: t.time):
                    engine.on_tick(tick)
                engine.finish()
                metadata = {"execution": "tick", "input_files": args.data}
            else:
                bars = read_bars(args.data)
                if not bars:
                    raise ValueError("Input contains no bars")
                engine = run_backtest(config, bars, args.start, args.end)
                metadata = {"execution": "conservative_minute", "coverage": coverage(bars), "start": args.start, "end": args.end}
            print(json.dumps(write_report(engine, args.output, metadata), indent=2))
            print(f"Report: {Path(args.output).resolve() / 'report.html'}")
        elif args.command == "compare":
            from .validation import compare_windows
            config = Config.load(args.config)
            if args.schedule_file:
                config.schedule_file = args.schedule_file
            print(json.dumps(compare_windows(config, read_bars(args.data), args.start, args.end, args.window_days, args.output), indent=2))
        elif args.command in ("paper", "probe"):
            from .paper import run_paper, run_rest_paper
            config = Config.load(args.config)
            if args.schedule_file:
                config.schedule_file = args.schedule_file
            if args.duration < 0 or args.warmup_days < 2:
                raise ValueError("duration must be nonnegative and warmup-days at least 2")
            if args.transport == "rest":
                print(json.dumps(run_rest_paper(config, args.contracts, args.journal, args.output,
                                               delay_seconds=args.delay_seconds, poll_seconds=args.poll_seconds,
                                               duration=args.duration, env_file=args.env_file, kill_file=args.kill_file,
                                               warmup_days=args.warmup_days, probe=args.command == "probe"), indent=2))
            else:
                print(json.dumps(run_paper(config, args.contracts, args.journal, args.output,
                                      feed=args.feed, duration=args.duration, env_file=args.env_file,
                                      kill_file=args.kill_file, warmup_days=args.warmup_days, probe=args.command == "probe"), indent=2))
        return 0
    except (ValueError, MassiveError, OSError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
