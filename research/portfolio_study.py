"""Raw-minute, causal shared-equity replay of the frozen CHoCH portfolio cases."""
from collections import Counter, defaultdict
from dataclasses import asdict
from hashlib import sha256
from heapq import merge
from itertools import groupby
from math import floor
from pathlib import Path
import argparse
import csv
import gzip
import json
import statistics
import time

from crt.models import Bar, INSTRUMENTS, MINUTE, timestamp
from crt.strategy import session_at, trading_day
from research.breadth_study import config, source_hashes
from research.expanded_markets import ExpandedEngine, market, register

PROTOCOL = Path('research/portfolio_protocol.json')
RESULTS = Path('research/results/portfolio')


class AssetEngine(ExpandedEngine):
    def __init__(self, owner, cfg, timeframe, rules):
        self.owner = None
        super().__init__(cfg, timeframe, 'retest_close', 2., rules)
        self.owner = owner

    @property
    def cash(self):
        return self.owner.cash if self.owner is not None else self._local_cash

    @cash.setter
    def cash(self, value):
        if self.owner is None:
            self._local_cash = value
        else:
            self.owner.cash = value

    @property
    def equity(self):
        return self.owner.equity if self.owner is not None else super().equity

    def _size(self, setup, entry, stop):
        qty, unit = super()._size(setup, entry, stop)
        if self.owner is None:
            return qty, unit
        return self.owner.size(self, setup, entry, qty, unit), unit

    def _enter_structural(self, setup, change, raw_price, at):
        before = self.equity
        if self.owner is not None:
            reason = self.owner.entry_block(self.market)
            if reason:
                self.owner.rejections[reason] += 1
                return False
        accepted = super()._enter_structural(setup, change, raw_price, at)
        if accepted and self.owner is not None:
            self.marks[setup.symbol] = raw_price
            p = self.positions[setup.symbol]
            self.owner.allocations.append(dict(time=at.isoformat(), symbol=setup.symbol, qty=p.qty,
                equity_before=before, equity=self.equity, risk=p.risk, total_risk=sum(x.risk for x in self.owner.positions.values()),
                reserved=self.owner.reserved(), positions=len(self.owner.positions)))
        return accepted


class Portfolio:
    def __init__(self, basket, equity, path, policy, rules, ready):
        self.policy, self.initial, self.cash = policy, float(equity), float(equity)
        self.day, self.day_start, self.halt = '', float(equity), ''
        self.peak, self.max_drawdown = float(equity), 0.
        self.engines, self.daily, self.allocations = {}, {}, []
        self.rejections, self.roll_marks = Counter(), []
        self.market_days, self.minute_count = set(), 0
        self.last_at = None
        for symbol, tf in basket.items():
            cfg = config(symbol, path, rules, ready[symbol]['schedule_file'])
            cfg.initial_equity = equity
            self.engines[symbol] = AssetEngine(self, cfg, tf, rules)

    @property
    def positions(self):
        return {s: p for e in self.engines.values() for s, p in e.positions.items()}

    @property
    def equity(self):
        return self.cash + sum((e.marks.get(s, p.entry)-p.entry)*p.setup.side*INSTRUMENTS[s].point_value*p.qty
                               for e in self.engines.values() for s, p in e.positions.items())

    def reserved(self):
        return sum((self.policy['futures_initial_margin_proxy'][s] if market(s).kind == 'futures'
                    else self.engines[s].marks.get(s, p.entry)*market(s).point_value)*p.qty
                   for s, p in self.positions.items())

    def entry_block(self, m):
        if self.halt:
            return self.halt
        if len(self.positions) >= self.policy['max_positions']:
            return 'max_positions'
        if m.group == 'equity' and sum(market(s).group == 'equity' for s in self.positions) >= self.policy['max_equity_index_positions']:
            return 'equity_index_concentration'
        return ''

    def size(self, engine, setup, entry, qty, unit):
        equity, p = self.equity, self.policy
        open_risk = sum(x.risk for x in self.positions.values())
        budget = min(equity*p['risk_fraction'], equity*p['max_open_risk_fraction']-open_risk,
                     self.day_start*p['daily_loss_fraction']-max(0., self.day_start-equity)-open_risk)
        fee = engine.config.commission_per_side[setup.symbol]
        reserve = p['futures_initial_margin_proxy'][setup.symbol] if engine.market.kind == 'futures' else entry*engine.market.point_value
        capital_qty = floor((equity*p['capital_utilization_limit']-self.reserved())/(reserve+fee))
        sized = max(0, min(qty, floor((budget+1e-8)/unit), capital_qty))
        if sized == 0:
            self.rejections['capital_or_risk_below_one_unit'] += 1
        return sized

    def observe(self, at):
        equity = self.equity
        self.peak = max(self.peak, equity)
        self.max_drawdown = max(self.max_drawdown, 100*(self.peak-equity)/self.peak)
        if not self.halt and self.day_start-equity >= self.day_start*self.policy['daily_loss_fraction']-1e-8:
            self.halt = 'portfolio_daily_loss'
            self.rejections['daily_halt_events'] += 1
        if not self.halt and self.reserved() > equity:
            self.halt = 'capital_deficit'
            self.rejections['capital_halt_events'] += 1
        self.daily[trading_day(at)] = equity

    def batch(self, bars, start, end):
        bars = [b for b in bars if b.symbol in self.engines]
        if not bars:
            return
        at = bars[0].start
        day = trading_day(at)
        if day >= end:
            return
        learn_only = day < start
        self.last_at = at+MINUTE
        # Book stale old-contract exits now, never retroactively in the ledger.
        for b in bars:
            e = self.engines[b.symbol]
            st = e.states[b.symbol]
            if st.contract is not None and st.contract != b.contract and b.symbol in e.positions:
                old = st.last_bar
                e._exit(b.symbol, e.marks[b.symbol], at, 'roll_data_end_mark', market=True)
                self.roll_marks.append(dict(symbol=b.symbol, last_mark=old.end.isoformat(), booked_at=at.isoformat()))
            e._contract(b.symbol, b.contract)
            if st.last_bar and b.start < st.last_bar.end:
                raise ValueError('Duplicate/overlapping bar')
            e.marks[b.symbol] = b.open
        if day != self.day:
            self.day, self.day_start, self.halt = day, self.equity, ''
        for b in bars:
            e = self.engines[b.symbol]
            e._clock(at)
            if self.halt:
                e.halt_reason = self.halt
        if not learn_only:
            self.observe(at)
            if any(market(b.symbol).kind == 'futures' for b in bars):
                self.market_days.add(day)
            # All existing opening-price exits precede all new entries.
            for b in bars:
                e = self.engines[b.symbol]
                if self.halt and b.symbol in e.positions:
                    e._exit(b.symbol, b.open, at, self.halt, market=True)
                e._path_price(b.symbol, b.open, at)
            # No symbol's later high/low can influence another opening order.
            for b in sorted(bars, key=lambda b: b.symbol):
                e = self.engines[b.symbol]
                order = e.orders.pop(b.symbol, None)
                if order is not None:
                    setup, change = order
                    if at == setup.created and session_at(at, e.config) == setup.session:
                        e._enter_structural(setup, change, b.open, at)
            for b in sorted(bars, key=lambda b: b.symbol):
                self.engines[b.symbol]._path_execution(b)
            for b in bars:
                e = self.engines[b.symbol]
                if b.symbol in e.waiting:
                    change, _ = e.waiting[b.symbol]
                    if (change.side == 1 and b.low <= change.stop) or (change.side == -1 and b.high >= change.stop):
                        e.waiting.pop(b.symbol)
        for b in bars:
            self.engines[b.symbol].marks[b.symbol] = b.close
        if not learn_only:
            self.observe(at)  # attribute this minute's ending mark to its own trading day
            self.minute_count += 1
        for b in bars:
            self.engines[b.symbol]._learn(b)

    def finish(self):
        for s, e in self.engines.items():
            if s in e.positions:
                e._exit(s, e.marks[s], self.last_at, 'end_of_data', market=True)
        if self.daily:
            self.daily[max(self.daily)] = self.equity
            self.peak = max(self.peak, self.equity)
            self.max_drawdown = max(self.max_drawdown, 100*(self.peak-self.equity)/self.peak)

    def result(self):
        trades = sorted((asdict(t) for e in self.engines.values() for t in e.trades), key=lambda t: (t['entry_time'], t['symbol']))
        assert abs(self.equity-self.initial-sum(t['net'] for t in trades)) < 1e-5
        days = sorted(self.market_days)
        daily, prior = [], self.initial
        # Closed/weekend dates have no entries or positions under these sessions.
        for day in days:
            value = self.daily[day]
            daily.append(dict(day=day, equity=value, pnl=value-prior, return_pct=100*(value/prior-1)))
            prior = value
        assert abs(prior-self.equity) < 1e-5
        counts = Counter(trading_day(timestamp(t['entry_time'])) for t in trades)
        win, loss = sum(max(t['net'], 0) for t in trades), -sum(min(t['net'], 0) for t in trades)
        return dict(stats=dict(initial_equity=self.initial, ending_equity=self.equity, net_pnl=self.equity-self.initial,
                    return_pct=100*(self.equity/self.initial-1), market_days=len(days), trades=len(trades),
                    active_days=len(counts), zero_entry_days=len(days)-len(counts), mean_trades_per_market_day=len(trades)/len(days),
                    mean_daily_return_pct=statistics.mean(d['return_pct'] for d in daily),
                    geometric_daily_return_pct=100*((self.equity/self.initial)**(1/len(days))-1),
                    mean_daily_pnl=(self.equity-self.initial)/len(days),
                    max_minute_mark_drawdown_pct=self.max_drawdown, profit_factor=win/loss if loss else None,
                    win_rate_pct=100*sum(t['net'] > 0 for t in trades)/len(trades) if trades else None,
                    worst_daily_return_pct=min(d['return_pct'] for d in daily), best_daily_return_pct=max(d['return_pct'] for d in daily),
                    trades_by_symbol=dict(Counter(t['symbol'] for t in trades)), daily_trade_count_histogram=dict(Counter(counts.get(d, 0) for d in days)),
                    max_positions=max((a['positions'] for a in self.allocations), default=0),
                    max_reserved_fraction=max((a['reserved']/a['equity'] for a in self.allocations), default=0)),
                    daily=daily, trades=trades, allocations=self.allocations, rejections=dict(self.rejections), roll_marks=self.roll_marks)


def symbol_bars(ready):
    prior = None
    for record in sorted(ready['records'], key=lambda r: r['start']):
        with Path(record['file']).open(newline='', encoding='utf-8-sig') as stream:
            for r in csv.DictReader(stream):
                b = Bar(r['symbol'], timestamp(r['timestamp']), *(float(r[k]) for k in ('open', 'high', 'low', 'close')),
                        float(r['volume']), r['contract'])
                if prior is not None and b.start < prior:
                    raise ValueError('Input is not chronologically unique')
                prior = b.end
                yield b


def run(basket_name, initial, path):
    register()
    policy = json.loads(PROTOCOL.read_text())
    rules = json.loads(Path('research/choch_breadth_protocol.json').read_text())
    basket = policy['baskets'][basket_name]
    ready = {s: json.loads(Path(f'data/choch_expanded/READY_{s}.json').read_text()) for s in basket}
    inputs = {}
    for doc in ready.values():
        inputs.update(doc['input_sha256']);inputs[doc['schedule_file']] = doc['calendar_sha256']
    for file, expected in inputs.items():
        if sha256(Path(file).read_bytes()).hexdigest() != expected:
            raise ValueError('Input hash changed: '+file)
    p = Portfolio(basket, initial, path, policy, rules, ready)
    streams = [symbol_bars(doc) for doc in ready.values()]
    started = time.monotonic()
    for _, group in groupby(merge(*streams, key=lambda b: (b.start, b.symbol)), key=lambda b: b.start):
        p.batch(list(group), rules['start_trading_date'], rules['end_exclusive_trading_date'])
    p.finish()
    result = p.result()
    result.update(basket=basket_name, starting_equity=initial, path=path,
                  policy_sha256=sha256(PROTOCOL.read_bytes()).hexdigest(),
                  breadth_protocol_sha256=sha256(Path('research/choch_breadth_protocol.json').read_bytes()).hexdigest(),
                  source_sha256=source_hashes() | {str(Path(__file__)): sha256(Path(__file__).read_bytes()).hexdigest()},
                  input_sha256=inputs, seconds=time.monotonic()-started)
    RESULTS.mkdir(exist_ok=True)
    file = RESULTS/f'{basket_name}_{initial}_{path}.json.gz'
    with file.open('wb') as raw:
        with gzip.GzipFile(fileobj=raw, mode='wb', mtime=0) as stream:
            stream.write(json.dumps(result, allow_nan=False).encode())
    print(json.dumps(dict(basket=basket_name, equity=initial, path=path, stats=result['stats'], seconds=result['seconds'])), flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--basket', required=True)
    parser.add_argument('--equity', type=int, required=True)
    parser.add_argument('--path', choices=['high_first', 'low_first'], required=True)
    args = parser.parse_args()
    run(args.basket, args.equity, args.path)
