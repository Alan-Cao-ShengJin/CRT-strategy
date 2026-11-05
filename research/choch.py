"""Causal, research-only CHoCH implementation with a frozen structural stop."""
from collections import deque
from dataclasses import dataclass
from datetime import timedelta
from math import ceil

from crt.engine import Engine
from crt.models import Bar, INSTRUMENTS, MINUTE, Position, Setup
from crt.strategy import Aggregator, session_at


@dataclass(frozen=True)
class Pivot:
    index: int
    price: float
    time: object
    known_at: object


@dataclass(frozen=True)
class Change:
    symbol: str
    contract: str
    timeframe: int
    side: int
    level: float
    stop: float
    confirmed: object
    pivot: Pivot


class Structure:
    def __init__(self, timeframe, wing=2):
        self.timeframe, self.wing = timeframe, wing
        self.bars = []
        self.highs, self.lows = deque(maxlen=2), deque(maxlen=2)
        self.consumed = set()

    def push(self, bar):
        if self.bars and (bar.start != self.bars[-1].end or bar.contract != self.bars[-1].contract):
            self.__init__(self.timeframe, self.wing)
        i = len(self.bars)
        self.bars.append(bar)
        if len(self.bars) >= 2*self.wing+1:
            center = i-self.wing
            candidate = self.bars[center]
            neighbors = self.bars[center-self.wing:center]+self.bars[center+1:i+1]
            high = all(candidate.high > b.high for b in neighbors)
            low = all(candidate.low < b.low for b in neighbors)
            if high != low:
                pivot = Pivot(center, candidate.high if high else candidate.low, candidate.start, bar.end)
                (self.highs if high else self.lows).append(pivot)
        if len(self.highs) < 2 or len(self.lows) < 2 or len(self.bars) < 2:
            return None
        up = self.highs[-1].price > self.highs[-2].price and self.lows[-1].price > self.lows[-2].price
        down = self.highs[-1].price < self.highs[-2].price and self.lows[-1].price < self.lows[-2].price
        tick = INSTRUMENTS[bar.symbol].tick
        previous = self.bars[-2].close
        pivot, side = (self.lows[-1], -1) if up else (self.highs[-1], 1) if down else (None, 0)
        if pivot is None or (side, pivot.time) in self.consumed:
            return None
        if side*(previous-pivot.price) > 1e-8 or side*(bar.close-pivot.price) < tick-1e-8:
            return None
        self.consumed.add((side, pivot.time))
        opposite = self.highs[-1] if side == -1 else self.lows[-1]
        observed = self.bars[opposite.index:]
        stop = max(b.high for b in observed)+tick if side == -1 else min(b.low for b in observed)-tick
        return Change(bar.symbol, bar.contract, self.timeframe, side, pivot.price, round(stop, 8), bar.end, pivot)


class ChochEngine(Engine):
    def __init__(self, config, timeframe=5, mode='next_open', reward_risk=2., retest_bars=3, wing=2):
        super().__init__(config)
        if mode not in ('next_open', 'retest_close'):
            raise ValueError('Unknown CHoCH entry mode')
        self.timeframe, self.mode, self.reward_risk = timeframe, mode, reward_risk
        self.retest_bars, self.wing = retest_bars, wing
        self.aggregators, self.structures = {}, {}
        self.waiting, self.orders = {}, {}
        self.setup_details = {}
        self.max_fractional_drawdown = 0.
        self.entry_risk_fractions, self.entry_quantities = [], []
        self.roll_data_liquidations = []

    def _contract(self, symbol, contract):
        old = self.states[symbol].contract
        result = super()._contract(symbol, contract)
        if old != contract:
            self.aggregators[symbol] = Aggregator(self.timeframe)
            self.structures[symbol] = Structure(self.timeframe, self.wing)
            self.waiting.pop(symbol, None)
            self.orders.pop(symbol, None)
        return result

    def _risk_check(self, at):
        super()._risk_check(at)
        self.max_fractional_drawdown = max(self.max_fractional_drawdown, (self.peak-self.equity)/self.peak)

    def _queue(self, change, created, session):
        setup = Setup(f'choch:{change.symbol}:{change.timeframe}:{change.confirmed.isoformat()}:{self.mode}',
                      change.symbol, change.contract, change.side, change.timeframe,
                      created, created+MINUTE, change.level, change.level,
                      session, 'choch_'+self.mode, change.side)
        self.orders[change.symbol] = (setup, change)
        self.setup_details[setup.id] = dict(
            change_confirmed=change.confirmed.isoformat(), entry_confirmation=created.isoformat(),
            broken_pivot_time=change.pivot.time.isoformat(), pivot_known_at=change.pivot.known_at.isoformat(),
            broken_level=change.level, structural_stop=change.stop, timeframe=self.timeframe,
            entry_mode=self.mode, nominal_reward_risk=self.reward_risk)
        self.log('choch_order', created, setup_id=setup.id, symbol=change.symbol,
                 stop=change.stop, level=change.level, pivot_known_at=change.pivot.known_at.isoformat())

    def _learn(self, bar):
        state = self.states[bar.symbol]
        if state.last_bar and bar.start != state.last_bar.end:
            self.waiting.pop(bar.symbol, None)
        state.last_bar = bar
        completed = self.aggregators[bar.symbol].push(bar)
        if completed is None:
            return
        b, symbol = completed, bar.symbol
        waiting = self.waiting.get(symbol)
        if waiting is not None:
            change, session = waiting
            expires = change.confirmed+MINUTE*self.timeframe*self.retest_bars
            if b.end > expires or session_at(b.end, self.config) != session:
                self.waiting.pop(symbol)
            elif b.start >= change.confirmed:
                tick = INSTRUMENTS[symbol].tick
                touched = b.low <= change.level <= b.high
                accepted = change.side*(b.close-change.level) >= tick-1e-8
                if touched and accepted:
                    self._queue(change, b.end, session)
                    self.waiting.pop(symbol)
        change = self.structures[symbol].push(b)
        if change is None:
            return
        session = session_at(b.end, self.config)
        st = self.states[symbol]
        if (not session or session in st.selected or symbol in self.positions
                or symbol in self.waiting or symbol in self.orders or self.halt_reason
                or self._blackout(symbol, b.end)):
            return
        st.selected.add(session)
        self.log('choch', b.end, symbol=symbol, side=change.side, level=change.level,
                 stop=change.stop, pivot_time=change.pivot.time.isoformat(),
                 pivot_known_at=change.pivot.known_at.isoformat())
        if self.mode == 'next_open':
            self._queue(change, b.end, session)
        else:
            self.waiting[symbol] = (change, session)

    def _bar_execution(self, b):
        symbol = b.symbol
        if symbol in self.waiting:
            change, _ = self.waiting[symbol]
            if (change.side == 1 and b.low <= change.stop) or (change.side == -1 and b.high >= change.stop):
                self.waiting.pop(symbol)
                self.log('choch_invalidated', b.end, symbol=symbol, reason='structural_stop_before_entry')
        order = self.orders.pop(symbol, None)
        if order is not None:
            setup, change = order
            if b.start == setup.created and session_at(b.start, self.config) == setup.session:
                self._enter_structural(setup, change, b.open, b.start)
            else:
                self.log('choch_cancelled', b.start, symbol=symbol, reason='immediate_next_minute_missing_or_session_end')
        # The newly entered bracket participates in every remaining entry-bar
        # level, with the same explicit price paths as the CRT comparator.
        self._path_execution(b)

    def _enter_structural(self, setup, change, raw_price, at):
        c, symbol, side = self.config, setup.symbol, setup.side
        inst = INSTRUMENTS[symbol]
        self._risk_check(at)
        if (self.halt_reason or self.feed_halted or self._blackout(symbol, at)
                or not session_at(at, c) or self.day_trades >= c.max_trades_per_day
                or symbol in self.positions or len(self.positions) >= c.max_positions):
            return False
        if side*(raw_price-change.stop) <= 1e-8:
            self.log('choch_cancelled', at, symbol=symbol, reason='opening_gap_through_stop')
            return False
        price = round(raw_price+side*c.slippage_ticks*inst.tick, 8)
        distance_ticks = round(side*(price-change.stop)/inst.tick)
        if distance_ticks < c.min_stop_ticks:
            return False
        setup.target = round(price+side*ceil(distance_ticks*self.reward_risk-1e-8)*inst.tick, 8)
        before = self.equity
        qty, unit = self._size(setup, price, change.stop)
        if qty < 1:
            return False
        self.positions[symbol] = Position(setup, at, price, change.stop, qty, unit*qty, c.commission_per_side[symbol])
        self.marks[symbol] = price
        self.cash -= c.commission_per_side[symbol]*qty
        self.day_trades += 1
        self.entry_risk_fractions.append(unit*qty/before)
        self.entry_quantities.append(qty)
        self.log('entry', at, symbol=symbol, price=price, stop=change.stop, target=setup.target,
                 qty=qty, setup_id=setup.id)
        return True
