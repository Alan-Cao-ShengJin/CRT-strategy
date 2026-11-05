# Transcript interpretation

Both source files were read in full. Transcript 2 supplies the clearest mechanical specification; Transcript 1 adds examples of discretionary bias, chop, rebreak choice, and occasional runners. The source contains marketing, anecdotal results, and incomplete visual references. Those are not independent performance evidence.

## Mechanical rules retained

| Topic | Source evidence | Implementation |
|---|---|---|
| Setup chart | Transcript 2, whiteboard discussion of 15/30/60 minutes | Watch all three from completed minute bars |
| C1 | Mark wick high and low; roll forward until a sweep forms | Adjacent completed HTF bars become C1/C2 |
| Bullish C2 | Sweep C1 low and close inside C1 | Strict lower low and strictly internal close |
| Bearish C2 | Inverse of bullish setup | Strict higher high and strictly internal close |
| Entry | Detailed interview explicitly breaks C2 high for longs, C2 low for shorts | One tick beyond the relevant C2 extreme; configurable |
| “Middle/midpoint” wording | Chart narration sometimes calls the C2 level “middle” | Interpreted as the middle candle's extreme, not its 50% price |
| Target | Opposite wick of C1 | C1 high for long, C1 low for short |
| Stop | Set target first, adjust stop to 1.5R | Reward distance / 1.5; round stop distance down to exchange ticks |
| Direct | Stop-limit entry, brackets preplanned in interview | Trigger with a two-tick price cap; adverse fill allowance within cap |
| Rebreak | First break, pullback, second break; can happen within the same HTF candle | Explicit waiting → broken → armed state machine |
| Expiry | Interview asks whether a C4 breakout still counts; speaker says no | No new entry at or after C3 end |
| Trade duration | An entered position may take additional candles to reach target | Hold fixed bracket, subject to operational/account exits |
| Sessions | Transcript 2 gives 09:30–12:00 and 20:00–22:00 Eastern | ZoneInfo America/New_York; automatic DST |
| Trade frequency | Usually one or two trades daily | Two total portfolio entries per CME trading date |

All three contracts use the same signal rules and their own tick size and point value. No special profitability assumptions are made for gold or Nasdaq.

## Decisions needed to turn discretion into code

1. **Hourly bias.** The speaker marks extreme swing levels and judges the strength of reactions. The deterministic proxy uses the preceding 24 completed hourly bars' extremes. A fresh excursion beyond an extreme followed by a close back through it and a rejection of at least half the current hourly range sets a directional bias for eight hours. Two consecutive hourly candle bodies beyond that level invalidate it. Without an active reversal, three successive higher highs/higher lows or lower highs/lower lows provide a trend fallback. Conflicting double sweeps produce no new reversal bias. This is an approximation, not a claim to reproduce the speaker's trained eye.
2. **Chop.** Over the last 20 consecutive minute bars, compute mean wick fraction, body-direction flips, and close-to-close directional efficiency. Low efficiency together with very high wicks or frequent flips blocks a setup. The configurable thresholds are engineering assumptions, not transcript-supplied numbers.
3. **First setup.** Select the first setup that passes bias/chop/target filters per instrument per session. A tie among timeframes prefers 15, then 30, then 60 minutes. Simultaneous instruments use configured symbol priority. An expired selected setup consumes that instrument's session opportunity. Global entry caps still apply.
4. **Neutral markets.** Default to skipping. The optional `first` policy reflects examples in Transcript 1 where the speaker trades the first CRT without a clear bias.
5. **Sweep ambiguity.** Reject C2 candles that sweep both C1 extremes. The source does not give an unambiguous direction rule for that case.
6. **C2 body.** Require the closing price inside C1, not C2's entire open/close body. The interview's mechanical condition concerns the close; the descriptions are not perfectly consistent.
7. **Pre-session candles.** C1 and C2 may form before the trading window, but C2 must finish inside it. A setup completing exactly at 09:30 is eligible. Hourly bars align to New York clock hours, not rolling 09:30-based hours.
8. **Rebreak depth.** One tick back across the entry trigger arms a rebreak. The source permits shallow or deep pullbacks without a numeric definition. If C1's target has already traded before a rebreak entry, cancel the setup.
9. **Automatic entry choice.** `auto` uses the already-observed wick fraction to select rebreak in rougher conditions. Default is always rebreak, avoiding an undocumented discretionary switch.
10. **Account management.** Fixed-dollar/fractional risk, correlation caps, daily loss/drawdown halts, data-health controls, and scheduled liquidation extend the source's manual risk practice. Halts are not guarantees against gap losses. Operational exits are explicitly logged.

Order blocks, all-time-high intuition, optional 1.3R stops, discretionary manual exits, and runners are not automated. The primary interview repeatedly emphasizes a fixed full-position 1.5R bracket; implementing the loosely specified extensions would require additional assumptions. No profit-based parameter optimization was performed.

## Execution assumptions

Actual trade ticks preserve observed break/pullback/rebreak order; the first incomplete streamed minute is not used to form HTF candles. Stop-limit entries can remain unfilled after gaps. Protective stops simulate market execution at the first observed price beyond the stop plus adverse slippage. Targets receive no favorable gap improvement. Nominal R:R is calculated before costs; net R is lower, particularly for narrow ranges.

Minute OHLC cannot identify the sequence of every high, low, stop, and target. The corrected simulator explicitly assumes either open → high → low → close (`high_first`, the default) or open → low → high → close (`low_first`). It visits order levels along those legs, including entries, pullbacks, stops and targets in the same minute. A later extreme cannot retroactively cancel an earlier valid entry. These are hypothetical paths, not reconstructed ticks or rigorous best/worst bounds; additional oscillations and gaps are unobserved. Both paths must be compared for profitability research. Cross-instrument decisions follow symbol priority, so the annual study uses independent instrument accounts to avoid claiming a precise portfolio intrabar sequence.

The earlier one-transition-per-minute model is retained as `legacy` only to reproduce archived reports. It omitted entry-minute targets and could discard valid rebreak winners using a later extreme. Those results are biased and should not be used to judge profitability. Tick-mode execution continues to use actual observed trade sequencing.

Open positions are closed at a backtest's last available mark with an `end_of_data` reason. Missing data cannot guarantee a scheduled exit; the next observed price is used when the engine resumes. An interrupted paper process retains positions without inventing fills.

## Performance interpretation

The transcripts quote 70–80% wins, sometimes higher for rebreaks. The code does not embed those numbers as expectations. Real validation must distinguish mechanical pattern behavior, discretionary-proxy quality, bar-resolution penalties, fees, regime changes, and data quality. Study representative tick-level samples and independent forward paper results before drawing conclusions from a bar backtest.
