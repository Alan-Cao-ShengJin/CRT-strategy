"""Small Massive Futures API client. Keys stay in Authorization headers."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, date, timedelta
from pathlib import Path

from .models import Bar, Tick, INSTRUMENTS, UTC


class MassiveError(RuntimeError):
    pass


def api_key(env_file=".env"):
    if value := os.environ.get("MASSIVE_API_KEY"):
        return value
    path = Path(env_file)
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip().removeprefix("export ") == "MASSIVE_API_KEY":
                value = value.strip().strip("\"'")
                if value:
                    return value
    raise MassiveError("Set MASSIVE_API_KEY in the environment or .env")


def contract_root(ticker):
    match = re.fullmatch(r"(MNQ|MES|MGC)[FGHJKMNQUVXZ]\d{1,4}", ticker)
    if not match:
        raise ValueError(f"Use a dated outright contract such as MNQZ6, not {ticker!r}")
    return match.group(1)


class MassiveClient:
    BASE = "https://api.massive.com"

    def __init__(self, key=None, env_file=".env"):
        self.key = key or api_key(env_file)
        self.rate_file = Path(env_file).resolve().parent / ".massive_rate.sqlite"
        self.interval = max(0., float(os.environ.get("MASSIVE_REQUEST_INTERVAL_SECONDS", "13")))

    def _wait_request_slot(self):
        # Shared across CLI processes so a downloader cannot starve the paper feed.
        with sqlite3.connect(self.rate_file, timeout=30) as db:
            db.execute("CREATE TABLE IF NOT EXISTS rate_limit (id INTEGER PRIMARY KEY, next_time REAL)")
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT next_time FROM rate_limit WHERE id=1").fetchone()
            slot = max(time.time(), row[0] if row else 0.)
            db.execute("INSERT OR REPLACE INTO rate_limit VALUES (1, ?)", (slot + self.interval,))
        delay = slot - time.time()
        while delay > 0:
            time.sleep(min(delay, 15))
            delay = slot - time.time()

    def request(self, path, params=None):
        if path.startswith("http"):
            parsed = urllib.parse.urlsplit(path)
            if parsed.scheme != "https" or parsed.netloc != "api.massive.com":
                raise MassiveError("Refused untrusted pagination URL")
            query = [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query) if k.lower() != "apikey"]
            url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), ""))
        else:
            url = self.BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
        for attempt in range(4):
            self._wait_request_slot()
            req = urllib.request.Request(url, headers={"Authorization": "Bearer " + self.key, "User-Agent": "crt-futures/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    payload = json.load(response)
                if payload.get("status") in ("ERROR", "NOT_AUTHORIZED"):
                    raise MassiveError("Massive rejected this request; check futures entitlements")
                return payload
            except urllib.error.HTTPError as exc:
                if exc.code == 429 or exc.code >= 500:
                    if attempt < 3:
                        try:
                            wait = min(120., max(1., float(exc.headers.get("Retry-After", 60 if exc.code == 429 else 2 ** attempt))))
                        except ValueError:
                            wait = 60.
                        while wait > 0:
                            step = min(wait, 15.)
                            time.sleep(step)
                            wait -= step
                        continue
                # Never print request URLs or raw server errors containing credentials.
                raise MassiveError(f"Massive HTTP {exc.code}; verify futures plan, date range and contract") from None
            except (urllib.error.URLError, TimeoutError):
                if attempt == 3:
                    raise MassiveError("Massive connection failed after retries") from None
                time.sleep(2 ** attempt)

    def pages(self, path, params=None):
        visited = set()
        for _ in range(10000):
            payload = self.request(path, params)
            yield from payload.get("results", [])
            path, params = payload.get("next_url"), None
            if not path:
                return
            if path in visited:
                raise MassiveError("Repeated pagination cursor")
            visited.add(path)
        raise MassiveError("Pagination safety limit reached")

    def contracts(self, root, as_of):
        if root not in INSTRUMENTS:
            raise ValueError("Unknown product")
        params = {"product_code": root, "date": as_of, "active": "true", "limit": 1000}
        # Earlier API definitions have null/mislabeled contract type; regex excludes spreads.
        return [r for r in self.pages("/futures/v1/contracts", params)
                if re.fullmatch(re.escape(root) + r"[FGHJKMNQUVXZ]\d{1,4}", r.get("ticker", ""))]

    def bars(self, ticker, start, end, resolution="1min"):
        root = contract_root(ticker)
        params = {"resolution": resolution, "window_start.gte": start, "window_start.lt": end,
                  "sort": "window_start.asc", "limit": 50000}
        return self.pages(f"/futures/v1/aggs/{ticker}", params)

    def download(self, ticker, start, end):
        root = contract_root(ticker)
        output, seen = [], set()
        for row in self.bars(ticker, start, end):
            at = datetime.fromtimestamp(int(row["window_start"]) // 1_000_000_000, UTC)
            if at in seen:
                continue
            seen.add(at)
            output.append(Bar(root, at, *(float(row[k]) for k in ("open", "high", "low", "close")), float(row.get("volume", 0)), ticker))
        return sorted(output, key=lambda b: b.start)

    def schedules(self, root, start, end):
        return list(self.pages("/futures/v1/schedules", {"product_code": root, "session_end_date.gte": start,
                                                      "session_end_date.lte": end, "limit": 1000}))

    def latest(self, ticker):
        contract_root(ticker)
        return self.request(f"/futures/v1/aggs/{ticker}", {"resolution": "1min", "sort": "window_start.desc", "limit": 1}).get("results", [])

    def validate_contract(self, ticker, as_of, *, paper=False):
        root = contract_root(ticker)
        records = [r for r in self.contracts(root, as_of) if r["ticker"] == ticker]
        if not records:
            raise MassiveError(f"{ticker} is not active on {as_of}")
        row = records[0]
        tick_size = row.get("trade_tick_size")
        if tick_size is not None and abs(float(tick_size) - INSTRUMENTS[root].tick) > 1e-8:
            raise MassiveError(f"Exchange tick size changed for {ticker}; review instrument configuration")
        if paper:
            last = date.fromisoformat(row["last_trade_date"])
            at = date.fromisoformat(as_of)
            # MGC: refuse the delivery month and the seven calendar days before it.
            match = re.fullmatch(r"MGC([FGHJKMNQUVXZ])(\d+)", ticker)
            if match:
                month = "FGHJKMNQUVXZ".index(match.group(1)) + 1
                delivery_start = date(last.year, month, 1)
                if at >= delivery_start - timedelta(days=7):
                    raise MassiveError(f"{ticker} is too close to its delivery month; select a later contract")
            elif (last - at).days <= 7:
                raise MassiveError(f"{ticker} is within seven days of expiry; select a later contract")
        return row


def parse_trade(message):
    if message.get("ev") != "T":
        return None
    ticker = message["sym"]
    root = contract_root(ticker)
    return Tick(root, datetime.fromtimestamp(message["t"] / 1000, UTC), float(message["p"]),
                float(message.get("s", 0)), ticker, str(message.get("q", "")))
