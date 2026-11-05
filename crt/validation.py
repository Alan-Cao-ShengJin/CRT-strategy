"""Fixed parameters across held-out windows; no in-sample parameter selection."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

from .cli import run_backtest
from .report import write_report


def compare_windows(config, bars, start, end, window_days, output):
    begin, finish = date.fromisoformat(start), date.fromisoformat(end)
    if finish <= begin or window_days < 1:
        raise ValueError("Invalid validation window")
    records = []
    while begin < finish:
        until = min(finish, begin + timedelta(days=window_days))
        # Every test window starts a fresh account; preceding five days only warm indicators.
        warm = (begin - timedelta(days=5)).isoformat()
        subset = [b for b in bars if warm <= b.start.date().isoformat() <= until.isoformat()]
        for mode in ("direct", "rebreak"):
            cfg = replace(config, entry_mode=mode)
            engine = run_backtest(cfg, subset, begin.isoformat(), until.isoformat())
            destination = Path(output) / f"{begin}_{until}" / mode
            summary = write_report(engine, destination, {"start": str(begin), "end": str(until), "parameters_frozen": True})
            records.append({"start": str(begin), "end": str(until), "mode": mode, **summary})
        begin = until
    Path(output).mkdir(parents=True, exist_ok=True)
    (Path(output) / "comparison.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    return records
