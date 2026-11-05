from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from math import isfinite
from zoneinfo import ZoneInfo

UTC = timezone.utc
NY = ZoneInfo("America/New_York")
MINUTE = timedelta(minutes=1)


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamps must include Z or a UTC offset")
    return result.astimezone(UTC)


@dataclass(frozen=True)
class Instrument:
    symbol: str
    tick: float
    point_value: float
    exchange: str
    group: str

    @property
    def tick_value(self) -> float:
        return self.tick * self.point_value

    def on_grid(self, price: float) -> bool:
        return isfinite(price) and abs(price / self.tick - round(price / self.tick)) < 1e-6


INSTRUMENTS = {
    "MNQ": Instrument("MNQ", .25, 2., "CME", "equity"),
    "MES": Instrument("MES", .25, 5., "CME", "equity"),
    "MGC": Instrument("MGC", .1, 10., "COMEX", "gold"),
}


@dataclass(frozen=True)
class Bar:
    """One minute, start-stamped in UTC. Larger bars are internal only."""
    symbol: str
    start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.
    contract: str = ""
    minutes: int = 1

    def __post_init__(self):
        if self.symbol not in INSTRUMENTS:
            raise ValueError(f"Unsupported symbol: {self.symbol}")
        if self.start.tzinfo is None or self.start.utcoffset() != timedelta(0):
            raise ValueError("Bar timestamps must be UTC-aware")
        prices = (self.open, self.high, self.low, self.close)
        if not all(isfinite(p) and p > 0 and INSTRUMENTS[self.symbol].on_grid(p) for p in prices):
            raise ValueError(f"Invalid or off-tick prices for {self.symbol}")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("Inconsistent OHLC")
        if not isfinite(self.volume) or self.volume < 0 or self.minutes < 1:
            raise ValueError("Invalid volume or interval")
        if self.start.second or self.start.microsecond:
            raise ValueError("Bars must start on a minute boundary")

    @property
    def end(self):
        return self.start + MINUTE * self.minutes

    def to_dict(self):
        return {**asdict(self), "start": self.start.isoformat()}

    @classmethod
    def from_dict(cls, value):
        return cls(**{**value, "start": timestamp(value["start"])})


@dataclass(frozen=True)
class Tick:
    symbol: str
    time: datetime
    price: float
    size: float = 1.
    contract: str = ""
    sequence: str = ""

    def __post_init__(self):
        if self.symbol not in INSTRUMENTS or not INSTRUMENTS[self.symbol].on_grid(self.price) or self.price <= 0:
            raise ValueError("Invalid tick symbol or price")
        if self.time.tzinfo is None or self.time.utcoffset() != timedelta(0):
            raise ValueError("Tick timestamps must be UTC-aware")
        if not isfinite(self.size) or self.size < 0:
            raise ValueError("Invalid tick size")

    def to_dict(self):
        return {**asdict(self), "time": self.time.isoformat()}

    @classmethod
    def from_dict(cls, value):
        return cls(**{**value, "time": timestamp(value["time"])})


@dataclass
class Setup:
    id: str
    symbol: str
    contract: str
    side: int
    timeframe: int
    created: datetime
    expires: datetime
    trigger: float
    target: float
    session: str
    mode: str
    bias: int
    phase: str = "waiting"
    activated: bool = False


@dataclass
class Position:
    setup: Setup
    time: datetime
    entry: float
    stop: float
    qty: int
    risk: float
    fee_side: float


@dataclass
class Trade:
    setup_id: str
    symbol: str
    contract: str
    side: int
    timeframe: int
    mode: str
    entry_time: str
    exit_time: str
    entry: float
    exit: float
    stop: float
    target: float
    qty: int
    gross: float
    fees: float
    net: float
    risk: float
    r_multiple: float
    reason: str
