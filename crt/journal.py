"""Transactional input journal; deterministic replay, no executable snapshots."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from .engine import Engine
from .models import Bar, Tick


class Journal:
    def __init__(self, path, config):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS inputs (id INTEGER PRIMARY KEY, event_key TEXT UNIQUE, payload TEXT NOT NULL)")
        signature = json.dumps({"version": 1, "config": config.to_dict(),
                                "blackout_content": Path(config.blackout_file).read_text() if config.blackout_file else "",
                                "schedule_content": Path(config.schedule_file).read_text() if config.schedule_file else ""}, sort_keys=True)
        old = self.db.execute("SELECT value FROM metadata WHERE key='config'").fetchone()
        if old and old[0] != signature:
            self.db.close()
            raise ValueError("Journal configuration differs; use a new journal for new settings")
        self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('config', ?)", (signature,))
        self.db.commit()
        self.engine = Engine(config)
        for payload, in self.db.execute("SELECT payload FROM inputs ORDER BY id"):
            self._apply(json.loads(payload))

    def _apply(self, event):
        if event["kind"] == "bars":
            self.engine.on_bar_batch([Bar.from_dict(b) for b in event["bars"]], learn_only=event.get("learn_only", False))
        elif event["kind"] == "tick":
            self.engine.on_tick(Tick.from_dict(event["tick"]))
        elif event["kind"] == "feed":
            self.engine.feed_halted = event["halted"]
            if event["halted"]:
                for state in self.engine.states.values():
                    state.pending = None
        elif event["kind"] == "warmup_ready":
            for state in self.engine.states.values():
                state.pending = None
                state.selected.clear()
        else:
            raise ValueError("Unknown journal event")

    def append(self, event, key=None):
        payload = json.dumps(event, sort_keys=True)
        if key is None:
            if event["kind"] == "tick":
                t = event["tick"]
                key = "tick:" + t["symbol"] + ":" + t["contract"] + ":" + t["time"][:10] + ":" + (t.get("sequence") or hashlib.sha256(payload.encode()).hexdigest())
            elif event["kind"] == "bars":
                key = "bars:" + event["bars"][0]["start"]
        if key and (existing := self.db.execute("SELECT payload FROM inputs WHERE event_key=?", (key,)).fetchone()):
            if existing[0] != payload:
                raise ValueError("Duplicate event identity with different content")
            return False
        with self.db:
            self.db.execute("INSERT INTO inputs(event_key,payload) VALUES (?,?)", (key, payload))
            self._apply(event)
        return True

    def close(self):
        self.db.close()
