"""User-maintained news and exchange closures; no invented holiday schedule."""
import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from .models import timestamp
from .config import Config


@dataclass
class Blackout:
    start: object
    end: object
    symbols: list[str]
    reason: str


def load_blackouts(config: Config) -> list[Blackout]:
    if not config.blackout_file:
        return []
    output = []
    for row in json.loads(Path(config.blackout_file).read_text()):
        if "event_time" in row:
            at = timestamp(row["event_time"])
            start = at - timedelta(minutes=config.news_before_minutes)
            end = at + timedelta(minutes=config.news_after_minutes)
        else:
            start, end = timestamp(row["start"]), timestamp(row["end"])
        if end <= start:
            raise ValueError("Blackout end must follow start")
        output.append(Blackout(start, end, row.get("symbols", ["MNQ", "MES", "MGC"]), row["reason"]))
    return output


def load_sessions(path, symbols=None):
    """Pair Massive open/close events, deduplicating repeated exchange records."""
    if not path:
        return {}
    result = {}
    events = json.loads(Path(path).read_text())
    for symbol in (symbols if symbols is not None else ("MNQ", "MES", "MGC")):
        unique = sorted({(r["timestamp"], r["event"]) for r in events if r.get("product_code") == symbol and r.get("event") in ("open", "close", "halt", "resume")})
        intervals, opened = [], None
        for at, kind in unique:
            if kind in ("open", "resume"):
                opened = timestamp(at)
            elif opened is not None:
                closed = timestamp(at)
                if closed > opened:
                    intervals.append((opened, closed))
                opened = None
        result[symbol] = intervals
    return result
