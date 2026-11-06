"""Causal ORB screening features; no fitting or strategy-return calculation."""
from dataclasses import dataclass
from math import isfinite, prod


@dataclass(frozen=True)
class Daily:
    day: str
    high: float
    low: float
    close: float
    volume: float
    opening_volume: float
    valid: bool = True


@dataclass(frozen=True)
class Opening:
    day: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    minutes: int = 5


@dataclass(frozen=True)
class Split:
    day: str
    split_from: float
    split_to: float


def share_factor(old_day, decision_day, events):
    """Only executed events; a future split cannot change a past decision."""
    ratios = []
    for s in events:
        if old_day < s.day <= decision_day:
            if not isfinite(s.split_from) or not isfinite(s.split_to) or min(s.split_from, s.split_to) <= 0:
                raise ValueError('Invalid share adjustment')
            ratios.append(s.split_to/s.split_from)
    return prod(ratios)


def features(ticker, opening, history, prior_dates, splits=()):
    """Use exactly the supplied prior 15 exchange sessions, never future rows.

    Inputs are as-traded prices/volumes. ATR is a declared SMA interpretation;
    this does not claim exact reproduction of undocumented author code.
    """
    if len(prior_dates) != 15 or len(set(prior_dates)) != 15 or sorted(prior_dates) != list(prior_dates):
        raise ValueError('Supply 15 distinct ordered prior exchange sessions')
    if prior_dates[-1] >= opening.day:
        raise ValueError('Lookback must finish before decision day')
    if opening.minutes != 5:
        return None
    prices = [opening.open, opening.high, opening.low, opening.close]
    if not all(isfinite(x) and x > 0 for x in prices) or not isfinite(opening.volume) or opening.volume <= 0:
        return None
    if opening.high < max(opening.open, opening.close) or opening.low > min(opening.open, opening.close):
        raise ValueError('Invalid opening OHLC')
    bydate = {h.day: h for h in history if h.day < opening.day}
    if any(day not in bydate or not bydate[day].valid for day in prior_dates):
        return None
    normalized = []
    for day in prior_dates:
        h = bydate[day]
        values = (h.high, h.low, h.close, h.volume, h.opening_volume)
        if not all(isfinite(x) and x > 0 for x in values) or not h.low <= h.close <= h.high:
            return None
        factor = share_factor(day, opening.day, splits)
        normalized.append((h.high/factor, h.low/factor, h.close/factor,
                           h.volume*factor, h.opening_volume*factor))
    true_ranges = [max(h-l, abs(h-normalized[i-1][2]), abs(l-normalized[i-1][2]))
                   for i, (h, l, _, _, _) in enumerate(normalized) if i]
    average_volume = sum(h[3] for h in normalized[1:])/14
    reference_opening_volume = sum(h[4] for h in normalized[1:])/14
    atr = sum(true_ranges)/14
    side = 1 if opening.close > opening.open else -1 if opening.close < opening.open else 0
    return dict(ticker=ticker, day=opening.day, open=opening.open, side=side,
                opening_high=opening.high, opening_low=opening.low, atr=atr,
                average_daily_volume=average_volume,
                relative_volume=opening.volume/reference_opening_volume,
                lookback_end=prior_dates[-1], stop_distance=.1*atr)


def select(rows, *, mode='relative_volume', maximum=20):
    if mode not in ('relative_volume', 'prior_volume') or maximum < 1:
        raise ValueError('Invalid selection policy')
    if len({r['ticker'] for r in rows}) != len(rows):
        raise ValueError('Duplicate ticker in decision snapshot')
    eligible = [r for r in rows if r['open'] > 5 and r['atr'] > .5
                and r['average_daily_volume'] >= 1000000 and r['side'] != 0]
    if mode == 'relative_volume':
        eligible = [r for r in eligible if r['relative_volume'] >= 1]
    key = 'relative_volume' if mode == 'relative_volume' else 'average_daily_volume'
    return sorted(eligible, key=lambda r: (-r[key], r['ticker']))[:maximum]
