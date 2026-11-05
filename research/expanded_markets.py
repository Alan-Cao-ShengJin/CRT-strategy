"""Opt-in research market definitions; importing this module does not register them."""
from dataclasses import dataclass
from datetime import timedelta
from math import ceil, floor

from crt.calendar import load_sessions
from crt.models import Bar, INSTRUMENTS, Instrument, MINUTE
from research.choch import ChochEngine, Structure


@dataclass(frozen=True)
class Market:
    symbol: str
    kind: str
    tick: float
    point_value: float
    exchange: str
    group: str
    api_symbol: str = ''


EXTRA = {
    'MYM': Market('MYM', 'futures', 1., .5, 'CBOT', 'equity'),
    'M2K': Market('M2K', 'futures', .1, 5., 'CME', 'equity'),
    'MCL': Market('MCL', 'futures', .01, 100., 'NYMEX', 'energy'),
    'M6E': Market('M6E', 'futures', .0001, 12500., 'CME', 'fx'),
    'SIL': Market('SIL', 'futures', .005, 1000., 'COMEX', 'metals'),
    'BTCUSD': Market('BTCUSD', 'crypto', .01, .001, 'CRYPTO', 'crypto', 'X:BTCUSD'),
    **{s: Market(s, 'stock', .01, 1., 'US_STOCKS', 'stock', s)
       for s in ('AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMZN', 'JPM', 'XOM', 'UNH')}
}


def market(symbol):
    if symbol in EXTRA:
        return EXTRA[symbol]
    i = INSTRUMENTS[symbol]
    return Market(symbol, 'futures', i.tick, i.point_value, i.exchange, i.group)


def register():
    """Called explicitly inside isolated research/download processes only."""
    for s, m in EXTRA.items():
        spec = Instrument(s, m.tick, m.point_value, m.exchange, m.group)
        if s in INSTRUMENTS and INSTRUMENTS[s] != spec:
            raise ValueError(f'Conflicting research definition: {s}')
        INSTRUMENTS[s] = spec


class StockAggregator:
    """Complete buckets anchored to the regular session open, including 60m."""
    def __init__(self, timeframe, intervals):
        self.minutes, self.intervals, self.parts = timeframe, intervals, []
        self.offset = 0

    def push(self, bar):
        if self.parts and bar.start != self.parts[-1].end:
            self.parts.clear()
        while self.offset < len(self.intervals) and self.intervals[self.offset][1] <= bar.start:
            self.offset += 1
        if self.offset == len(self.intervals):
            return None
        start, end = self.intervals[self.offset]
        if not start <= bar.start < end:
            return None
        offset = round((bar.start-start)/MINUTE)
        if offset % self.minutes == 0:
            self.parts = [bar]
        elif self.parts:
            self.parts.append(bar)
        if len(self.parts) != self.minutes:
            return None
        parts, self.parts = self.parts, []
        return Bar(bar.symbol, parts[0].start, parts[0].open, max(x.high for x in parts),
                   min(x.low for x in parts), parts[-1].close, sum(x.volume for x in parts),
                   bar.contract, self.minutes)


class StockStructure(Structure):
    """Bridge scheduled overnight closures, but never missing complete buckets."""
    def __init__(self, timeframe, intervals, wing=2):
        super().__init__(timeframe, wing)
        starts = [start+n*timeframe*MINUTE for start, end in intervals
                  for n in range(int((end-start)/MINUTE)//timeframe)]
        self.ordinal = {at: i for i, at in enumerate(starts)}

    def push(self, bar):
        bridge = bool(self.bars and self.ordinal.get(bar.start, -2) == self.ordinal.get(self.bars[-1].start, -4)+1)
        # Structure's reset intentionally reconstructs the base state; preserve
        # the exchange schedule index across unexpected-gap resets.
        if self.bars and ((bar.start != self.bars[-1].end and not bridge) or bar.contract != self.bars[-1].contract):
            Structure.__init__(self, self.timeframe, self.wing)
        return super().push(bar, bridge_gap=bridge)


class SpotStructure(Structure):
    def push(self, bar):
        change = super().push(bar)
        return change if change is not None and change.side == 1 else None


class ExpandedEngine(ChochEngine):
    def __init__(self, config, timeframe, mode, reward_risk=2., rules=None):
        self.market = market(config.symbols[0])
        self.rules = rules or {}
        super().__init__(config, timeframe, mode, reward_risk)
        self.exchange_sessions = load_sessions(config.schedule_file, config.symbols)
        self.complete_candles = 0

    def _contract(self, symbol, contract):
        old = self.states[symbol].contract
        state = super()._contract(symbol, contract)
        if old != contract and self.market.kind == 'stock':
            intervals = self.exchange_sessions[symbol]
            self.aggregators[symbol] = StockAggregator(self.timeframe, intervals)
            self.structures[symbol] = StockStructure(self.timeframe, intervals)
        elif old != contract and self.market.kind == 'crypto':
            self.structures[symbol] = SpotStructure(self.timeframe)
        return state

    def _learn(self, bar):
        prior = self.structures[bar.symbol].bars[-1].end if self.structures[bar.symbol].bars else None
        super()._learn(bar)
        after = self.structures[bar.symbol].bars[-1].end if self.structures[bar.symbol].bars else None
        self.complete_candles += int(after is not None and after != prior)

    def _slip_ticks(self, price):
        if self.market.kind != 'crypto':
            return self.config.slippage_ticks
        return ceil(price*self.rules['crypto_slippage_bps']/10000/self.market.tick-1e-8)

    def _size(self, setup, entry, stop):
        if self.market.kind == 'futures':
            return super()._size(setup, entry, stop)
        m, c = self.market, self.config
        stop_fill = stop-setup.side*self._slip_ticks(stop)*m.tick
        if m.kind == 'crypto':
            rate = self.rules['crypto_fee_rate']
            fees = (entry+abs(stop_fill))*m.point_value*rate
        else:
            fees = 2*self.rules['stock_commission_per_share']
        unit = abs(entry-stop_fill)*m.point_value+fees
        qty = floor(min(self.equity*c.risk_fraction/unit,
                        self.equity*self.rules['cash_asset_notional_cap']/(
                            entry*m.point_value+(entry*m.point_value*self.rules['crypto_fee_rate'] if m.kind == 'crypto'
                                                 else self.rules['stock_commission_per_share']))))
        return max(0, min(c.max_contracts, qty)), unit

    def _enter_structural(self, setup, change, raw_price, at):
        if self.market.kind != 'crypto':
            return super()._enter_structural(setup, change, raw_price, at)
        if setup.side != 1:
            return False
        c, s = self.config, setup.symbol
        old_ticks, old_fee = c.slippage_ticks, c.commission_per_side[s]
        c.slippage_ticks = self._slip_ticks(raw_price)
        fill = round(raw_price+setup.side*c.slippage_ticks*self.market.tick, 8)
        c.commission_per_side[s] = fill*self.market.point_value*self.rules['crypto_fee_rate']
        try:
            return super()._enter_structural(setup, change, raw_price, at)
        finally:
            c.slippage_ticks, c.commission_per_side[s] = old_ticks, old_fee

    def _exit(self, symbol, price, at, reason, market=False):
        if self.market.kind != 'crypto':
            return super()._exit(symbol, price, at, reason, market=market)
        p = self.positions[symbol]
        old_ticks, old_losses = self.config.slippage_ticks, self.losses
        self.config.slippage_ticks = self._slip_ticks(price)
        try:
            super()._exit(symbol, price, at, reason, market=market)
        finally:
            self.config.slippage_ticks = old_ticks
        trade = self.trades[-1]
        fee = trade.exit*self.market.point_value*self.rules['crypto_fee_rate']
        adjustment = (fee-p.fee_side)*p.qty
        self.cash -= adjustment
        trade.fees += adjustment
        trade.net -= adjustment
        trade.r_multiple = trade.net/trade.risk
        self.losses = old_losses+1 if trade.net < 0 else 0
        if self.events and self.events[-1]['kind'] == 'exit':
            self.events[-1]['net'] = trade.net
            self.events[-1]['fees'] = trade.fees
