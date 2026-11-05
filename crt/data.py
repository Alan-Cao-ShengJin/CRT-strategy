from __future__ import annotations

import csv
import json
from itertools import groupby
from pathlib import Path

from .models import Bar, Tick, timestamp


def read_bars(paths: list[str]) -> list[Bar]:
    rows = []
    seen = set()
    for path in paths:
        with open(path, newline="", encoding="utf-8-sig") as stream:
            for line, row in enumerate(csv.DictReader(stream), 2):
                try:
                    bar = Bar(row["symbol"], timestamp(row["timestamp"]),
                              *(float(row[k]) for k in ("open", "high", "low", "close")),
                              float(row.get("volume") or 0), row.get("contract", ""))
                    key = (bar.symbol, bar.start)
                    if key in seen:
                        raise ValueError("Duplicate symbol/timestamp (including overlapping contracts)")
                    seen.add(key)
                    rows.append(bar)
                except (ValueError, KeyError) as exc:
                    raise ValueError(f"{Path(path).name}:{line}: {exc}") from None
    return sorted(rows, key=lambda b: (b.start, b.symbol))


def write_bars(path, bars):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["timestamp", "symbol", "contract", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        for b in bars:
            writer.writerow({"timestamp": b.start.isoformat(), "symbol": b.symbol, "contract": b.contract,
                             "open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume})
    temporary.replace(path)


def batches(bars):
    for _, group in groupby(bars, key=lambda b: b.start):
        yield list(group)


def read_ticks(path):
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield Tick.from_dict(json.loads(line))


def coverage(bars):
    result = {}
    for symbol in sorted({b.symbol for b in bars}):
        subset = [b for b in bars if b.symbol == symbol]
        result[symbol] = {"bars": len(subset), "first": subset[0].start.isoformat(),
                          "last": subset[-1].end.isoformat(),
                          "contracts": sorted({b.contract for b in subset}),
                          "gaps_over_one_minute": sum(b.start != a.end for a, b in zip(subset, subset[1:]))}
    return result
