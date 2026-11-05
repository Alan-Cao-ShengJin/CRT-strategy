from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

from .data import batches
from .journal import Journal
from .massive import MassiveClient, MassiveError, parse_trade, contract_root
from .models import UTC
from .report import write_report


def run_paper(config, tickers, journal_path, output, *, feed="realtime", duration=0, env_file=".env", kill_file="STOP", warmup_days=5, probe=False):
    from websockets.sync.client import connect
    client = MassiveClient(env_file=env_file)
    if len({contract_root(t) for t in tickers}) != len(tickers):
        raise ValueError("Only one dated contract per root may be subscribed")
    if set(contract_root(t) for t in tickers) != set(config.symbols):
        raise ValueError("Paper contracts must cover exactly the configured symbols")
    now = datetime.now(UTC)
    for ticker in tickers:
        client.validate_contract(ticker, now.date().isoformat(), paper=True)
    address = "wss://socket.massive.com/futures" if feed == "realtime" else "wss://delayed.massive.com/futures"
    lag = 0 if feed == "realtime" else 600
    journal = Journal(journal_path, config)
    engine = journal.engine
    received, rejected, event_count = 0, 0, len(engine.events)
    last_seen = {}
    last_report = time.monotonic()
    started = time.monotonic()
    from pathlib import Path
    try:
        # Open first; messages buffer while a fresh account obtains historical context.
        with connect(address, open_timeout=20, ping_interval=20, ping_timeout=20, max_queue=65536) as ws:
            ws.send(json.dumps({"action": "auth", "params": client.key}))
            authenticated = False
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                messages = json.loads(ws.recv(timeout=10))
                for m in messages if isinstance(messages, list) else [messages]:
                    if m.get("status") == "auth_success":
                        authenticated = True
                    elif m.get("status") in ("auth_failed", "error"):
                        detail = str(m.get("message", "")).replace(client.key, "[redacted]")[:250]
                        raise MassiveError("Massive WebSocket authentication failed: " + detail)
                if authenticated:
                    break
            if not authenticated:
                raise MassiveError("WebSocket authentication timed out")
            ws.send(json.dumps({"action": "subscribe", "params": ",".join("T." + t for t in tickers)}))
            print(json.dumps({"status": "authenticated", "feed": feed, "contracts": tickers, "mode": "paper"}), flush=True)
            if not probe:
                if engine.last_time is None:
                    cutoff = (datetime.now(UTC) - timedelta(seconds=lag)).replace(second=0, microsecond=0)
                    begin = cutoff - timedelta(days=warmup_days)
                    bars = []
                    for ticker in tickers:
                        bars.extend(client.download(ticker, str(int(begin.timestamp()) * 1_000_000_000), str(int(cutoff.timestamp()) * 1_000_000_000)))
                    if not bars:
                        raise MassiveError("No warmup data returned")
                    for batch in batches(sorted(bars, key=lambda b: (b.start, b.symbol))):
                        journal.append({"kind": "bars", "bars": [b.to_dict() for b in batch], "learn_only": True})
                    journal.append({"kind": "warmup_ready"})
                else:
                    # Missing tick history cannot reconstruct prior exits. Liquidate restored
                    # positions on next fresh price and halt new entries for this run.
                    journal.append({"kind": "feed", "halted": True})
                    print(json.dumps({"status": "recovered", "new_entries_halted": True,
                                      "message": "Use a fresh journal to start a new paper account; restored positions exit on fresh prices."}), flush=True)
            while not duration or time.monotonic() - started < duration:
                if Path(kill_file).exists() and not probe:
                    journal.append({"kind": "feed", "halted": True})
                    if not engine.positions:
                        break
                try:
                    raw = ws.recv(timeout=2)
                except TimeoutError:
                    if not probe and time.monotonic() - last_report >= config.max_tick_gap_seconds:
                        journal.append({"kind": "feed", "halted": True})
                    continue
                messages = json.loads(raw)
                for message in messages if isinstance(messages, list) else [messages]:
                    if message.get("ev") == "status":
                        if message.get("status") in ("error", "auth_failed"):
                            raise MassiveError("Massive rejected the futures trade subscription; check entitlements")
                        continue
                    tick = parse_trade(message)
                    if not tick or tick.contract not in tickers:
                        continue
                    age = (datetime.now(UTC) - tick.time).total_seconds() - lag
                    if age > config.max_feed_age_seconds or age < -5:
                        rejected += 1
                        if not probe:
                            journal.append({"kind": "feed", "halted": True})
                        continue
                    received += 1
                    if probe:
                        print(json.dumps({"status": "tick_received", "symbol": tick.symbol, "age_seconds": round(age, 2)}), flush=True)
                        return {"authenticated": True, "ticks": received}
                    st = engine.states[tick.symbol]
                    if st.last_bar and tick.time < st.last_bar.end:
                        continue
                    # Cross-symbol events can arrive out of order. Ignore late prints rather
                    # than travelling backward in time or rewriting a closed candle.
                    if engine.last_time and tick.time < engine.last_time:
                        rejected += 1
                        continue
                    previous = last_seen.get(tick.symbol)
                    if previous and (tick.time - previous).total_seconds() > config.max_tick_gap_seconds:
                        journal.append({"kind": "feed", "halted": True})
                    last_seen[tick.symbol] = tick.time
                    journal.append({"kind": "tick", "tick": tick.to_dict()})
                    for event in engine.events[event_count:]:
                        print(json.dumps(event), flush=True)
                    event_count = len(engine.events)
                if time.monotonic() - last_report >= 10:
                    write_report(engine, output, {"feed": feed, "received_ticks": received, "rejected_ticks": rejected})
                    print(json.dumps({"status": "heartbeat", **engine.summary()}), flush=True)
                    last_report = time.monotonic()
        return {"authenticated": True, "ticks": received, "rejected_ticks": rejected}
    except KeyboardInterrupt:
        print("Stopped. Paper positions remain in the journal; no broker orders exist.", flush=True)
    except (OSError, TimeoutError) as exc:
        raise MassiveError(f"WebSocket connection stopped ({type(exc).__name__}); journal retained") from None
    finally:
        if not probe:
            journal.append({"kind": "feed", "halted": True})
            write_report(engine, output, {"feed": feed, "received_ticks": received, "rejected_ticks": rejected,
                                          "stopped": True, "note": "Open simulated positions are retained, not silently marked closed."})
        journal.close()


def run_rest_paper(config, tickers, journal_path, output, *, delay_seconds=0, poll_seconds=60,
                   duration=0, env_file=".env", kill_file="STOP", warmup_days=5, probe=False):
    """Completed-minute simulation. An explicit delay never masquerades as live data."""
    from pathlib import Path
    from .models import MINUTE
    if delay_seconds < 0 or poll_seconds < 15:
        raise ValueError("delay-seconds must be nonnegative; poll-seconds must be at least 15")
    if set(contract_root(t) for t in tickers) != set(config.symbols) or len(tickers) != len(config.symbols):
        raise ValueError("Specify exactly one dated contract for every configured symbol")
    client = MassiveClient(env_file=env_file)
    observed = {}
    for ticker in tickers:
        latest = client.latest(ticker)
        if not latest:
            raise MassiveError(f"No REST data available for {ticker}")
        last = datetime.fromtimestamp(max(int(x["window_start"]) for x in latest) // 1_000_000_000, UTC)
        observed[ticker] = {"latest_bar_utc": last.isoformat(),
                            "age_seconds": round((datetime.now(UTC)-last-MINUTE).total_seconds())}
    if probe:
        return {"transport": "rest", "data": observed}
    print(json.dumps({"transport": "rest", "simulation_delay_seconds": delay_seconds, "data": observed}), flush=True)
    # During an open market a stale subscription is actionable; on weekends no new bars
    # are expected, so report staleness and wait without consuming a past session as live.
    from .strategy import market_open
    if market_open(datetime.now(UTC)-timedelta(seconds=delay_seconds)):
        if any(v["age_seconds"] - delay_seconds > config.max_feed_age_seconds + 120 for v in observed.values()):
            raise MassiveError("REST data is stale for the chosen simulation clock. Use probe --transport rest and explicitly set --delay-seconds to your feed delay")
    for ticker in tickers:
        client.validate_contract(ticker, datetime.now(UTC).date().isoformat(), paper=True)
    journal = Journal(journal_path, config)
    engine = journal.engine
    started, processed = time.monotonic(), 0
    try:
        if engine.last_time is None:
            cutoff = (datetime.now(UTC)-timedelta(seconds=delay_seconds)).replace(second=0, microsecond=0)
            begin = cutoff - timedelta(days=warmup_days)
            bars = []
            for ticker in tickers:
                bars.extend(client.download(ticker, str(int(begin.timestamp())*1_000_000_000), str(int(cutoff.timestamp())*1_000_000_000)))
            for batch in batches(sorted(bars, key=lambda b: (b.start, b.symbol))):
                journal.append({"kind": "bars", "bars": [b.to_dict() for b in batch], "learn_only": True})
            journal.append({"kind": "warmup_ready"})
        else:
            # REST can backfill every completed minute after the persisted cursor.
            journal.append({"kind": "feed", "halted": False})
        while True:
            if Path(kill_file).exists():
                journal.append({"kind": "feed", "halted": True})
                if not engine.positions:
                    break
            cutoff = (datetime.now(UTC)-timedelta(seconds=delay_seconds)).replace(second=0, microsecond=0)
            cursors = {s: st.last_bar.end if st.last_bar else cutoff for s, st in engine.states.items()}
            rows = []
            for ticker in tickers:
                begin = cursors[contract_root(ticker)]
                if begin < cutoff:
                    rows.extend(client.download(ticker, str(int(begin.timestamp())*1_000_000_000), str(int(cutoff.timestamp())*1_000_000_000)))
            # A common watermark makes all configured instruments participate in portfolio
            # decisions before the cursor advances; an unavailable symbol stalls the batch.
            latest = {s: max([b.end for b in rows if b.symbol == s] + [cursors[s]]) for s in config.symbols}
            watermark = min(latest.values())
            for batch in batches(sorted((b for b in rows if b.end <= watermark), key=lambda b: (b.start, b.symbol))):
                if engine.last_time and batch[0].start < engine.last_time:
                    continue
                journal.append({"kind": "bars", "bars": [b.to_dict() for b in batch]})
                processed += len(batch)
            write_report(engine, output, {"transport": "rest", "simulation_delay_seconds": delay_seconds,
                                          "processed_new_bars": processed, "observed_feed": observed})
            print(json.dumps({"status": "heartbeat", "simulation_delay_seconds": delay_seconds, **engine.summary()}), flush=True)
            if duration and time.monotonic()-started >= duration:
                break
            time.sleep(min(poll_seconds, max(.1, duration-(time.monotonic()-started))) if duration else poll_seconds)
        return {"transport": "rest", "processed_new_bars": processed, **engine.summary()}
    except KeyboardInterrupt:
        return {"status": "stopped", **engine.summary()}
    finally:
        write_report(engine, output, {"transport": "rest", "simulation_delay_seconds": delay_seconds, "stopped": True})
        journal.close()
