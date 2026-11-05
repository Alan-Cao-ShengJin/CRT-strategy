from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, time

from .config import Config
from .models import Bar, Setup, INSTRUMENTS, NY, MINUTE


def trading_day(at: datetime) -> str:
    local = at.astimezone(NY)
    return (local.date() + timedelta(days=int(local.hour >= 18))).isoformat()


def market_open(at: datetime) -> bool:
    local = at.astimezone(NY)
    wd, hm = local.weekday(), local.time()
    if wd == 5 or (wd == 6 and hm < time(18)) or (wd == 4 and hm >= time(17)):
        return False
    return not time(17) <= hm < time(18)


def session_at(at: datetime, config: Config) -> str | None:
    if not market_open(at):
        return None
    local = at.astimezone(NY)
    if config.skip_mondays and local.weekday() == 0:
        return None
    for name, (start, end) in config.sessions.items():
        if time.fromisoformat(start) <= local.time() < time.fromisoformat(end):
            return f"{local.date().isoformat()}:{name}"
    return None


class Aggregator:
    """Only emits complete, consecutive buckets; gaps never fabricate candles."""
    def __init__(self, minutes: int):
        self.minutes = minutes
        self.parts: list[Bar] = []

    def push(self, bar: Bar) -> Bar | None:
        if self.parts and (bar.start != self.parts[-1].end or bar.contract != self.parts[-1].contract):
            self.parts.clear()
        local = bar.start.astimezone(NY)
        if local.minute % self.minutes == 0:
            self.parts = [bar]
        elif self.parts:
            self.parts.append(bar)
        if len(self.parts) != self.minutes:
            return None
        parts, self.parts = self.parts, []
        return Bar(bar.symbol, parts[0].start, parts[0].open, max(x.high for x in parts),
                   min(x.low for x in parts), parts[-1].close, sum(x.volume for x in parts),
                   bar.contract, self.minutes)


@dataclass
class Chop:
    wick_fraction: float
    flip_fraction: float
    efficiency: float
    blocked: bool


def chop_metrics(bars: list[Bar], config: Config) -> Chop:
    if len(bars) < config.chop_lookback:
        return Chop(1., 1., 0., True)
    bars = bars[-config.chop_lookback:]
    wicks = sum(1 - abs(b.close - b.open) / max(b.high - b.low, INSTRUMENTS[b.symbol].tick) for b in bars) / len(bars)
    signs = [1 if b.close > b.open else -1 if b.close < b.open else 0 for b in bars]
    flips = sum(a * b < 0 for a, b in zip(signs, signs[1:])) / (len(bars) - 1)
    path = sum(abs(b.close - a.close) for a, b in zip(bars, bars[1:]))
    efficiency = abs(bars[-1].close - bars[0].close) / path if path else 0.
    blocked = efficiency < config.chop_efficiency and (wicks >= config.chop_wick_fraction or flips >= config.chop_flip_fraction)
    return Chop(wicks, flips, efficiency, blocked)


class Bias:
    """Causal proxy for discretionary hourly levels, with explicit expiry."""
    def __init__(self, config: Config):
        self.config = config
        self.bars: deque[Bar] = deque(maxlen=config.bias_lookback)
        self.side = 0
        self.level = 0.
        self.expires: datetime | None = None
        self.invalidations = 0

    def push(self, bar: Bar):
        c = self.config
        if len(self.bars) >= c.bias_lookback:
            low, high = min(x.low for x in self.bars), max(x.high for x in self.bars)
            span = max(bar.high - bar.low, INSTRUMENTS[bar.symbol].tick)
            bull = bar.low < low < bar.close and (bar.close - bar.low) / span >= c.bias_rejection_fraction
            bear = bar.high > high > bar.close and (bar.high - bar.close) / span >= c.bias_rejection_fraction
            if bull != bear:
                self.side, self.level = (1, low) if bull else (-1, high)
                self.expires = bar.end + timedelta(hours=c.bias_ttl_hours)
                self.invalidations = 0
            elif bull and bear:
                self.side, self.expires = 0, None
            elif self.side:
                beyond = (max(bar.open, bar.close) < self.level if self.side == 1 else min(bar.open, bar.close) > self.level)
                self.invalidations = self.invalidations + 1 if beyond else 0
                if self.invalidations >= 2:
                    self.side, self.expires = 0, None
        self.bars.append(bar)

    def get(self, at: datetime) -> int:
        if len(self.bars) < self.config.bias_lookback:
            return 0
        if self.side and self.expires and at < self.expires:
            return self.side
        recent = list(self.bars)[-3:]
        if all(b.high > a.high and b.low > a.low for a, b in zip(recent, recent[1:])):
            return 1
        if all(b.high < a.high and b.low < a.low for a, b in zip(recent, recent[1:])):
            return -1
        return 0


def detect(c1: Bar, c2: Bar, bias: int, chop: Chop, config: Config) -> Setup | None:
    if not config.bias_enabled:
        bias = 0
    if c1.end != c2.start or c1.contract != c2.contract or c1.minutes != c2.minutes:
        return None
    session = session_at(c2.end, config)
    if not session or not c1.low < c2.close < c1.high:
        return None
    swept_low, swept_high = c2.low < c1.low, c2.high > c1.high
    # A double sweep is ambiguous and must never pick a direction arbitrarily.
    if swept_low == swept_high:
        return None
    side = 1 if swept_low else -1
    if (bias and side != bias) or (not bias and config.neutral_policy == "skip"):
        return None
    if config.chop_enabled and chop.blocked:
        return None
    inst = INSTRUMENTS[c2.symbol]
    trigger = (c2.high if side == 1 else c2.low) + side * inst.tick * config.entry_offset_ticks
    target = c1.high if side == 1 else c1.low
    if side * (target - trigger) < inst.tick * config.min_target_ticks - 1e-8:
        return None
    mode = config.entry_mode
    if mode == "auto":
        mode = "rebreak" if chop.wick_fraction >= config.auto_rebreak_wick_fraction else "direct"
    sid = f"{c2.symbol}:{c2.contract}:{c2.minutes}:{c2.end.isoformat()}:{side}"
    return Setup(sid, c2.symbol, c2.contract, side, c2.minutes, c2.end,
                 c2.end + MINUTE * c2.minutes, trigger, target, session, mode, bias)
