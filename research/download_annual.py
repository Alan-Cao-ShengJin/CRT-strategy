"""Reproducible dated-contract history, using fixed calendar rolls (no volume hindsight)."""
from __future__ import annotations

import calendar
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from crt.data import write_bars
from crt.massive import MassiveClient
from crt.models import NY, UTC

START = datetime(2025, 10, 27, tzinfo=UTC)
END = datetime(2026, 11, 5, tzinfo=UTC)
DESTINATION = Path("data/annual")


def roll_schedule(root):
    months = (3, 6, 9, 12) if root != "MGC" else (2, 4, 6, 8, 10, 12)
    result = []
    for year in range(2025, 2028):
        for month in months:
            if root == "MGC":
                day = date(year, month, 1) - timedelta(days=7)
            else:
                fridays = [d for d in range(1, calendar.monthrange(year, month)[1]+1) if date(year, month, d).weekday() == 4]
                day = date(year, month, fridays[2]) - timedelta(days=7)
            roll = datetime(day.year, day.month, day.day, 18, tzinfo=NY).astimezone(UTC)
            result.append((roll, root + "FGHJKMNQUVXZ"[month-1] + str(year % 10)))
    return result


def main():
    DESTINATION.mkdir(parents=True, exist_ok=True)
    client, manifest = MassiveClient(), []
    for root in ("MNQ", "MES", "MGC"):
        begin = START
        for roll, ticker in roll_schedule(root):
            if roll <= begin:
                continue
            until = min(roll, END)
            path = DESTINATION / f"{ticker}_{begin.date()}_{until.date()}.csv"
            record = {"root": root, "contract": ticker, "start": begin.isoformat(), "end": until.isoformat(), "file": str(path)}
            if not path.exists():
                rows = client.download(ticker, str(int(begin.timestamp())*1_000_000_000), str(int(until.timestamp())*1_000_000_000))
                if not rows:
                    raise RuntimeError(f"No history for {ticker}")
                write_bars(path, rows)
                print(json.dumps({**record, "bars": len(rows)}), flush=True)
            else:
                print(json.dumps({**record, "cached": True}), flush=True)
            manifest.append(record)
            (DESTINATION / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            begin = until
            if begin >= END:
                break
    schedules = DESTINATION / "schedules.json"
    if not schedules.exists():
        rows = []
        for root in ("MNQ", "MES", "MGC"):
            rows.extend(client.schedules(root, "2025-10-26", "2026-11-06"))
            print(json.dumps({"schedules": root, "events_so_far": len(rows)}), flush=True)
        schedules.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(json.dumps({"status": "complete", "segments": len(manifest)}), flush=True)


if __name__ == "__main__":
    main()
