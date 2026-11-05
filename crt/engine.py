from __future__ import annotations

from collections import deque
from dataclasses import asdict
from datetime import datetime, time, timedelta
from math import floor

from .calendar import load_blackouts, load_sessions
from .config import Config
from .models import Bar, Tick, Position, Trade, Setup, INSTRUMENTS, NY, MINUTE
from .strategy import Aggregator, Bias, chop_metrics, detect, session_at, trading_day, market_open


class SymbolState:
    def __init__(self, config):
        self.aggregators = {t: Aggregator(t) for t in set(config.timeframes) | {60}}
        self.previous: dict[int, Bar] = {}
        self.minutes: deque[Bar] = deque(maxlen=config.chop_lookback)
        self.bias = Bias(config)
        self.last_bar: Bar | None = None
        self.last_tick: Tick | None = None
        self.tick_bar: Bar | None = None
        self.tick_bar_valid = False
        self.pending: Setup | None = None
        self.selected: set[str] = set()
        self.contract: str | None = None


class Engine:
    """One shared account; causal signals; deterministic stop-limit paper fills."""
    def __init__(self, config: Config):
        self.config = config.validate()
        self.states = {s: SymbolState(config) for s in config.symbols}
        self.positions: dict[str, Position] = {}
        self.trades: list[Trade] = []
        self.events: list[dict] = []
        self.marks: dict[str, float] = {}
        self.cash = config.initial_equity
        self.peak = config.initial_equity
        self.max_drawdown_seen = 0.
        self.day = ""
        self.day_start = config.initial_equity
        self.day_trades = 0
        self.losses = 0
        self.halt_reason = ""
        self.permanent_halt = False
        self.feed_halted = False
        self.blackouts = load_blackouts(config)
        self.exchange_sessions = load_sessions(config.schedule_file)
        self.curve: list[dict] = []
        self.last_time: datetime | None = None

    @property
    def equity(self):
        return self.cash + sum((self.marks.get(s, p.entry) - p.entry) * p.setup.side * INSTRUMENTS[s].point_value * p.qty for s, p in self.positions.items())

    def log(self, kind, at, **kwargs):
        self.events.append({"kind": kind, "time": at.isoformat(), **kwargs})

    def _contract(self, symbol, contract):
        st = self.states[symbol]
        if st.contract is not None and contract != st.contract:
            if symbol in self.positions:
                raise ValueError(f"Cannot roll {symbol} with an open position; close the old contract first")
            selected = st.selected
            self.states[symbol] = st = SymbolState(self.config)
            st.selected = selected
        st.contract = contract
        return st

    def _clock(self, at):
        if self.last_time is not None and at < self.last_time:
            raise ValueError("Events must be globally time sorted")
        self.last_time = at
        day = trading_day(at)
        if day != self.day:
            self.day, self.day_start = day, self.equity
            self.day_trades, self.losses = 0, 0
            if not self.permanent_halt:
                self.halt_reason = ""
            for st in self.states.values():
                st.selected = {x for x in st.selected if x.startswith(at.astimezone(NY).date().isoformat())}

    def _blackout(self, symbol, at):
        if self.exchange_sessions and not any(a <= at < b for a, b in self.exchange_sessions.get(symbol, [])):
            return "exchange_closed_or_schedule_missing"
        return next((b.reason for b in self.blackouts if symbol in b.symbols and b.start <= at < b.end), "")

    def _risk_check(self, at):
        equity = self.equity
        self.peak = max(self.peak, equity)
        self.max_drawdown_seen = max(self.max_drawdown_seen, self.peak - equity)
        if self.peak - equity >= self.config.max_drawdown:
            self.halt_reason, self.permanent_halt = "max_drawdown", True
        elif self.day_start - equity >= self.config.max_daily_loss:
            self.halt_reason = "daily_loss"
        elif self.losses >= self.config.max_consecutive_losses:
            self.halt_reason = "consecutive_losses"
        if self.halt_reason:
            for st in self.states.values():
                st.pending = None
        # Risk halt liquidates at each symbol's next observed price, never at a stale mark.

    def _must_flatten(self, p: Position, at):
        if self.exchange_sessions:
            interval = next(((a, b) for a, b in self.exchange_sessions.get(p.setup.symbol, []) if a <= at < b), None)
            if interval is None or at >= interval[1] - timedelta(minutes=5):
                return True
        local = at.astimezone(NY)
        opened = p.time.astimezone(NY)
        cutoff = time.fromisoformat(self.config.flatten_time)
        boundary = datetime.combine(opened.date() + timedelta(days=int(opened.time() >= cutoff)), cutoff, NY)
        return at >= boundary or not market_open(at)

    def _exit(self, symbol, price, at, reason, market=False):
        p = self.positions.pop(symbol)
        inst, side = INSTRUMENTS[symbol], p.setup.side
        if market:
            price -= side * self.config.slippage_ticks * inst.tick
        price = round(round(price / inst.tick) * inst.tick, 8)
        gross = (price - p.entry) * side * inst.point_value * p.qty
        fees = 2 * p.fee_side * p.qty
        net = gross - fees
        self.cash += gross - p.fee_side * p.qty
        self.losses = self.losses + 1 if net < 0 else 0
        s = p.setup
        self.trades.append(Trade(s.id, symbol, s.contract, side, s.timeframe, s.mode,
                                 p.time.isoformat(), at.isoformat(), p.entry, price, p.stop,
                                 s.target, p.qty, gross, fees, net, p.risk, net / p.risk, reason))
        self.log("exit", at, symbol=symbol, price=price, qty=p.qty, net=net, reason=reason)

    def _size(self, s, entry, stop):
        c, inst = self.config, INSTRUMENTS[s.symbol]
        open_risk = sum(p.risk for p in self.positions.values())
        budget = min(c.risk_per_trade, self.equity * c.risk_fraction,
                     c.max_open_risk - open_risk,
                     c.max_daily_loss - max(0., self.day_start - self.equity) - open_risk,
                     c.max_drawdown - (self.peak - self.equity) - open_risk)
        unit = abs(entry - stop) * inst.point_value + 2 * c.commission_per_side[s.symbol] + c.slippage_ticks * inst.tick_value
        return max(0, min(c.max_contracts, floor((budget + 1e-8) / unit))), unit

    def _enter(self, s, price, at):
        c, inst = self.config, INSTRUMENTS[s.symbol]
        self._risk_check(at)
        reason = self._blackout(s.symbol, at)
        if (self.halt_reason or self.feed_halted or reason or not session_at(at, c)
                or self.day_trades >= c.max_trades_per_day or s.symbol in self.positions
                or len(self.positions) >= c.max_positions):
            self.log("entry_rejected", at, symbol=s.symbol, reason=self.halt_reason or reason or "portfolio_or_session_limit")
            return False
        if inst.group == "equity" and sum(INSTRUMENTS[x].group == "equity" for x in self.positions) >= c.max_equity_positions:
            self.log("entry_rejected", at, symbol=s.symbol, reason="correlated_equity_limit")
            return False
        reward = s.side * (s.target - price)
        # Round distance DOWN, so exchange tick rounding cannot worsen nominal R:R.
        stop_ticks = floor(reward / c.reward_risk / inst.tick + 1e-7)
        if reward < c.min_target_ticks * inst.tick - 1e-8 or stop_ticks < c.min_stop_ticks:
            return False
        stop = round(price - s.side * stop_ticks * inst.tick, 8)
        qty, unit = self._size(s, price, stop)
        if qty < 1:
            self.log("entry_rejected", at, symbol=s.symbol, reason="insufficient_risk_budget")
            return False
        self.positions[s.symbol] = Position(s, at, price, stop, qty, unit * qty, c.commission_per_side[s.symbol])
        self.marks[s.symbol] = price
        self.cash -= c.commission_per_side[s.symbol] * qty
        self.day_trades += 1
        self.states[s.symbol].pending = None
        self.log("entry", at, symbol=s.symbol, price=price, stop=stop, target=s.target, qty=qty, setup_id=s.id)
        return True

    def _expire(self, symbol, at):
        st = self.states[symbol]
        if st.pending and (at >= st.pending.expires or session_at(at, self.config) != st.pending.session):
            self.log("expired", at, symbol=symbol, setup_id=st.pending.id)
            st.pending = None

    def _learn(self, bar):
        st, c = self.states[bar.symbol], self.config
        if st.last_bar and bar.start != st.last_bar.end:
            st.minutes.clear()
        st.minutes.append(bar)
        st.last_bar = bar
        completed = {tf: result for tf, agg in st.aggregators.items() if (result := agg.push(bar)) is not None}
        if 60 in completed:
            st.bias.push(completed[60])
        if not completed:
            return
        chop = chop_metrics(list(st.minutes), c)
        candidates = []
        for tf, result in completed.items():
            previous = st.previous.get(tf)
            if previous and tf in c.timeframes:
                setup = detect(previous, result, st.bias.get(bar.end), chop, c)
                if setup and self._accept_setup(setup, previous, result, chop):
                    candidates.append(setup)
            st.previous[tf] = result
        if st.pending or bar.symbol in self.positions or self.halt_reason or self.feed_halted:
            return
        for setup in sorted(candidates, key=lambda s: s.timeframe):
            if setup.session not in st.selected and not self._blackout(bar.symbol, setup.created):
                st.pending = setup
                st.selected.add(setup.session)
                self.log("setup", bar.end, **{k: v for k, v in asdict(setup).items() if k not in ("created", "expires")})
                break

    def _accept_setup(self, setup, c1, c2, chop):
        """Causal extension point; production defaults accept every detected setup."""
        return True

    def on_bar_batch(self, bars: list[Bar], *, learn_only=False):
        """Historical minute execution. Same-timestamp bars share one account mark."""
        if not bars:
            return
        at = bars[0].start
        if any(b.start != at or b.minutes != 1 for b in bars) or len({b.symbol for b in bars}) != len(bars):
            raise ValueError("Batch must contain one 1-minute bar per symbol at a common start")
        for b in bars:
            st = self._contract(b.symbol, b.contract)
            if st.last_bar and b.start < st.last_bar.end:
                raise ValueError("Duplicate or overlapping bar")
            self.marks[b.symbol] = b.open
        self._clock(at)
        self._risk_check(at)
        for b in sorted(bars, key=lambda b: self.config.symbols.index(b.symbol)):
            self._expire(b.symbol, at)
            if not learn_only:
                self._bar_execution(b)
        # Signals become available only AFTER this entire minute has finished.
        for b in bars:
            self.marks[b.symbol] = b.close
        self._risk_check(at + MINUTE)
        for b in bars:
            self._learn(b)
        self.curve.append({"time": (at + MINUTE).isoformat(), "equity": self.equity})

    def _bar_execution(self, b):
        if self.config.bar_model != "legacy":
            return self._path_execution(b)
        return self._legacy_bar_execution(b)

    def _path_execution(self, b):
        """Explicit hypothetical OHLC ordering, NOT reconstructed trade ticks.

        Visit order levels along each monotone leg. No later bar extreme is
        consulted before an earlier entry, pullback, stop or target event.
        """
        if b.symbol not in self.positions and self.states[b.symbol].pending is None:
            return
        points = ([b.open, b.high, b.low, b.close] if self.config.bar_model == "high_first"
                  else [b.open, b.low, b.high, b.close])
        self._path_price(b.symbol, points[0], b.start)
        for start, end in zip(points, points[1:]):
            current = start
            for _ in range(16):
                if abs(end-current) < 1e-9:
                    break
                levels = [end]
                if (position := self.positions.get(b.symbol)) is not None:
                    levels += [position.stop, position.setup.target]
                    levels += self._additional_position_levels(position)
                elif (setup := self.states[b.symbol].pending) is not None:
                    levels += [setup.target]
                    if setup.mode == "rebreak" and setup.phase == "broken":
                        levels.append(setup.trigger - setup.side * self.config.pullback_ticks * INSTRUMENTS[b.symbol].tick)
                    elif setup.activated:
                        levels.append(setup.trigger + setup.side * self.config.limit_offset_ticks * INSTRUMENTS[b.symbol].tick)
                    else:
                        levels.append(setup.trigger)
                if end > current:
                    following = min(x for x in levels if current + 1e-8 < x <= end + 1e-8)
                else:
                    following = max(x for x in levels if end - 1e-8 <= x < current - 1e-8)
                self._path_price(b.symbol, round(following, 8), b.start)
                current = following
            else:
                raise RuntimeError("Intrabar event processing did not converge")

    def _additional_position_levels(self, position):
        """Optional account risk barriers, visited before later OHLC extremes."""
        return []

    def _path_price(self, symbol, price, at):
        self.marks[symbol] = price
        self._risk_check(at)
        if (p := self.positions.get(symbol)) is not None:
            if self.halt_reason or self.feed_halted or self._must_flatten(p, at):
                self._exit(symbol, price, at, self.halt_reason or ("feed_halt" if self.feed_halted else "daily_flatten"), market=True)
            elif p.setup.side * (price-p.stop) <= 1e-8:
                self._exit(symbol, price, at, "stop", market=True)
            elif p.setup.side * (price-p.setup.target) >= -1e-8:
                self._exit(symbol, p.setup.target, at, "target")
        elif (s := self.states[symbol].pending) is not None and at >= s.created:
            if s.side * (price-s.target) >= -1e-8:
                self.states[symbol].pending = None
                self.log("cancelled", at, symbol=symbol, reason="target_already_reached")
            else:
                self._price_entry(s, symbol, price, at)
        self._risk_check(at)

    def _legacy_bar_execution(self, b):
        """Historical implementation retained solely to reproduce old reports."""
        symbol, at = b.symbol, b.start
        if symbol in self.positions:
            p = self.positions[symbol]
            if self.halt_reason or self.feed_halted or self._must_flatten(p, at):
                self._exit(symbol, b.open, at, self.halt_reason or ("feed_halt" if self.feed_halted else "daily_flatten"), market=True)
            else:
                self._bar_exit(b, p, existing=True)
            self._risk_check(at)
            return
        st = self.states[symbol]
        s = st.pending
        if not s or at < s.created:
            return
        side, trigger = s.side, s.trigger
        crossed = b.high >= trigger if side == 1 else b.low <= trigger
        if s.mode == "rebreak":
            if (side == 1 and b.high >= s.target) or (side == -1 and b.low <= s.target):
                st.pending = None
                self.log("cancelled", at, symbol=symbol, reason="target_already_reached")
                return
            # At most one phase transition per minute: do not infer an OHLC path.
            if s.phase == "waiting":
                if crossed:
                    s.phase = "broken"
                return
            if s.phase == "broken":
                if side * (b.close - trigger) <= -self.config.pullback_ticks * INSTRUMENTS[symbol].tick + 1e-8:
                    s.phase = "armed"
                return
        s.activated = s.activated or crossed
        if not s.activated:
            return
        inst = INSTRUMENTS[symbol]
        cap = trigger + side * self.config.limit_offset_ticks * inst.tick
        if side * (b.open - cap) > 1e-8:
            # A resting triggered limit fills only if the bar revisits the limit.
            if (side == 1 and b.low > cap) or (side == -1 and b.high < cap):
                return
            price = cap
        else:
            base = max(b.open, trigger) if side == 1 else min(b.open, trigger)
            price = min(base + inst.tick * self.config.slippage_ticks, cap) if side == 1 else max(base - inst.tick * self.config.slippage_ticks, cap)
        if self._enter(s, round(price, 8), at):
            self._bar_exit(b, self.positions[symbol], existing=False)

    def _bar_exit(self, b, p, existing):
        side = p.setup.side
        stop_hit = b.low <= p.stop if side == 1 else b.high >= p.stop
        target_hit = b.high >= p.setup.target if side == 1 else b.low <= p.setup.target
        stop_gap = side * (b.open - p.stop) <= 0
        target_gap = side * (b.open - p.setup.target) >= 0
        if existing and target_gap:
            self._exit(b.symbol, p.setup.target, b.start, "target")
        elif stop_hit:
            # Stop wins ambiguous bars. New entries never take same-bar favorable exits.
            px = b.open if existing and stop_gap else p.stop
            self._exit(b.symbol, px, b.start, "stop", market=True)
        elif existing and target_hit:
            self._exit(b.symbol, p.setup.target, b.start, "target")

    def on_tick(self, tick: Tick):
        st = self._contract(tick.symbol, tick.contract)
        if st.last_tick and tick.time < st.last_tick.time:
            raise ValueError("Out-of-order tick")
        # Complete the previous minute before processing the first tick of the new one.
        start = tick.time.replace(second=0, microsecond=0)
        if st.tick_bar and start > st.tick_bar.start:
            if st.tick_bar_valid:
                self._learn(st.tick_bar)
            st.tick_bar = None
        self._clock(tick.time)
        self.marks[tick.symbol] = tick.price
        self._risk_check(tick.time)
        self._expire(tick.symbol, tick.time)
        symbol, price, at = tick.symbol, tick.price, tick.time
        if symbol in self.positions:
            p = self.positions[symbol]
            if self.halt_reason or self.feed_halted or self._must_flatten(p, at):
                self._exit(symbol, price, at, self.halt_reason or ("feed_halt" if self.feed_halted else "daily_flatten"), market=True)
            elif p.setup.side * (price - p.stop) <= 0:
                self._exit(symbol, price, at, "stop", market=True)
            elif p.setup.side * (price - p.setup.target) >= 0:
                self._exit(symbol, p.setup.target, at, "target")
        elif (s := st.pending) and at >= s.created:
            if s.side * (price - s.target) >= 0:
                st.pending = None
                self.log("cancelled", at, symbol=symbol, reason="target_already_reached")
                s = None
            if s is None:
                # Still aggregate this tick below; no entry can chase a delivered target.
                pass
            else:
                self._tick_entry(s, tick)
        self._risk_check(at)
        if st.tick_bar:
            b = st.tick_bar
            st.tick_bar = Bar(symbol, start, b.open, max(b.high, price), min(b.low, price), price, b.volume + tick.size, tick.contract)
        else:
            st.tick_bar = Bar(symbol, start, price, price, price, price, tick.size, tick.contract)
            st.tick_bar_valid = bool(st.last_tick and start == st.last_tick.time.replace(second=0, microsecond=0) + MINUTE)
        st.last_tick = tick
        # Bounded sampling avoids one curve point per market trade.
        if not self.curve or self.curve[-1]["time"][:16] != at.isoformat()[:16]:
            self.curve.append({"time": at.isoformat(), "equity": self.equity})

    def _tick_entry(self, s, tick):
        self._price_entry(s, tick.symbol, tick.price, tick.time)

    def _price_entry(self, s, symbol, price, at):
        d = s.side * (price - s.trigger)
        ready = s.mode == "direct" or s.phase == "armed"
        if s.mode == "rebreak" and s.phase == "waiting" and d >= -1e-8:
            s.phase = "broken"
        elif s.mode == "rebreak" and s.phase == "broken" and d <= -self.config.pullback_ticks * INSTRUMENTS[symbol].tick + 1e-8:
            s.phase = "armed"
        elif ready:
            s.activated = s.activated or d >= -1e-8
            inst = INSTRUMENTS[symbol]
            cap = s.trigger + s.side * self.config.limit_offset_ticks * inst.tick
            if s.activated and s.side * (price - cap) <= 1e-8:
                fill = min(price + self.config.slippage_ticks * inst.tick, cap) if s.side == 1 else max(price - self.config.slippage_ticks * inst.tick, cap)
                self._enter(s, round(fill, 8), at)

    def finish(self):
        """Backtest liquidation at each contract's last known mark; explicitly tagged."""
        for symbol, p in list(self.positions.items()):
            st = self.states[symbol]
            at = st.last_tick.time if st.last_tick else st.last_bar.end
            self._exit(symbol, self.marks[symbol], at, "end_of_data", market=True)
        if self.last_time:
            final_time = max([self.last_time] + [st.last_bar.end for st in self.states.values() if st.last_bar])
            self.curve.append({"time": final_time.isoformat(), "equity": self.equity})

    def summary(self):
        wins = sum(t.net > 0 for t in self.trades)
        gains = sum(max(0., t.net) for t in self.trades)
        losses = -sum(min(0., t.net) for t in self.trades)
        peak, drawdown = self.config.initial_equity, 0.
        for point in self.curve:
            peak = max(peak, point["equity"])
            drawdown = max(drawdown, peak - point["equity"])
        return {"trades": len(self.trades), "win_rate": wins / len(self.trades) if self.trades else None,
                "net_pnl": sum(t.net for t in self.trades), "equity": self.equity,
                "profit_factor": gains / losses if losses else None,
                "mean_r": sum(t.r_multiple for t in self.trades) / len(self.trades) if self.trades else None,
                "max_drawdown": max(drawdown, self.max_drawdown_seen), "open_positions": len(self.positions),
                "halt_reason": self.halt_reason, "feed_halted": self.feed_halted}
