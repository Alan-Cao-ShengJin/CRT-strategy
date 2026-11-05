from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from math import isfinite

from .models import INSTRUMENTS


@dataclass
class Config:
    symbols: list[str] = field(default_factory=lambda: ["MNQ", "MES", "MGC"])
    timeframes: list[int] = field(default_factory=lambda: [15, 30, 60])
    entry_mode: str = "rebreak"
    bar_model: str = "high_first"
    bias_enabled: bool = True
    reward_risk: float = 1.5
    entry_offset_ticks: int = 1
    limit_offset_ticks: int = 2
    pullback_ticks: int = 1
    min_target_ticks: int = 8
    min_stop_ticks: int = 4
    initial_equity: float = 50000.
    risk_per_trade: float = 100.
    risk_fraction: float = .005
    max_contracts: int = 10
    max_positions: int = 2
    max_equity_positions: int = 1
    max_open_risk: float = 200.
    max_daily_loss: float = 300.
    max_drawdown: float = 2000.
    max_trades_per_day: int = 2
    max_consecutive_losses: int = 2
    commission_per_side: dict[str, float] = field(default_factory=lambda: {s: .85 for s in INSTRUMENTS})
    slippage_ticks: int = 1
    sessions: dict[str, list[str]] = field(default_factory=lambda: {"new_york": ["09:30", "12:00"], "asia": ["20:00", "22:00"]})
    flatten_time: str = "16:55"
    neutral_policy: str = "skip"
    bias_lookback: int = 24
    bias_ttl_hours: int = 8
    bias_rejection_fraction: float = .5
    chop_lookback: int = 20
    chop_enabled: bool = True
    chop_wick_fraction: float = .72
    chop_flip_fraction: float = .7
    chop_efficiency: float = .18
    auto_rebreak_wick_fraction: float = .5
    skip_mondays: bool = False
    news_before_minutes: int = 5
    news_after_minutes: int = 10
    blackout_file: str = ""
    schedule_file: str = ""
    max_feed_age_seconds: float = 30.
    max_tick_gap_seconds: float = 120.

    def validate(self):
        if not self.symbols or len(set(self.symbols)) != len(self.symbols) or any(s not in INSTRUMENTS for s in self.symbols):
            raise ValueError("symbols must be a unique subset of MNQ, MES, MGC")
        if not self.timeframes or len(set(self.timeframes)) != len(self.timeframes) or any(t not in (15, 30, 60) for t in self.timeframes):
            raise ValueError("timeframes must be a unique subset of 15, 30, 60")
        if self.entry_mode not in ("direct", "rebreak", "auto") or self.neutral_policy not in ("skip", "first"):
            raise ValueError("Invalid entry mode or neutral policy")
        if self.bar_model not in ("legacy", "high_first", "low_first"):
            raise ValueError("bar_model must be legacy, high_first or low_first")
        for name in ("reward_risk", "initial_equity", "risk_per_trade", "risk_fraction", "max_open_risk", "max_daily_loss", "max_drawdown", "max_feed_age_seconds", "max_tick_gap_seconds"):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, (float, int)) or not isfinite(v) or v <= 0:
                raise ValueError(f"{name} must be positive and finite")
        if self.risk_fraction > 1:
            raise ValueError("risk_fraction cannot exceed 1")
        for name in ("entry_offset_ticks", "pullback_ticks", "min_target_ticks", "min_stop_ticks", "max_contracts", "max_positions", "max_equity_positions", "max_trades_per_day", "max_consecutive_losses", "bias_lookback", "bias_ttl_hours", "chop_lookback"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("limit_offset_ticks", "slippage_ticks", "news_before_minutes", "news_after_minutes"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if self.chop_lookback < 3 or self.bias_lookback < 3:
            raise ValueError("Indicator lookbacks must be at least 3")
        for name in ("bias_rejection_fraction", "chop_wick_fraction", "chop_flip_fraction", "chop_efficiency", "auto_rebreak_wick_fraction"):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"{name} must be in [0,1]")
        if set(self.commission_per_side) != set(INSTRUMENTS) or any(not isfinite(x) or x < 0 for x in self.commission_per_side.values()):
            raise ValueError("Set nonnegative per-side costs for all three symbols")
        from datetime import time
        time.fromisoformat(self.flatten_time)
        for window in self.sessions.values():
            if len(window) != 2 or not time.fromisoformat(window[0]) < time.fromisoformat(window[1]):
                raise ValueError("Session windows must be ascending same-day HH:MM values")
        return self

    def to_dict(self):
        return asdict(self)

    @classmethod
    def load(cls, path: str | None = None):
        return cls(**(json.loads(Path(path).read_text()) if path else {})).validate()
