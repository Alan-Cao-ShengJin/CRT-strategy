# Research and source notes

Checked on 5 November 2026, after reading both local transcripts and before implementing the initial strategy. Follow-up checks informed the data connector and execution model.

| Contract | Price tick | USD per point per contract | USD per tick | Exchange |
|---|---:|---:|---:|---|
| MNQ | 0.25 | 2 | 0.50 | CME |
| MES | 0.25 | 5 | 1.25 | CME |
| MGC | 0.10 | 10 | 1.00 | COMEX |

MNQ's multiplier and tick come from [CME's MNQ product page](https://www.cmegroup.com/markets/equities/nasdaq/micro-e-mini-nasdaq-100.html). MES uses [CME contract specifications](https://www.cmegroup.com/markets/equities/sp/micro-e-mini-sandp-500.contractSpecs.html) and [CME's Micro E-mini FAQ](https://www.cmegroup.com/articles/faqs/micro-e-mini-equity-index-futures-frequently-asked-questions.html). MGC represents ten troy ounces and is a deliverable gold contract; see [CME's gold overview](https://www.cmegroup.com/education/courses/event-contracts-underlying-markets/product-gold) and [MGC specifications](https://www.cmegroup.com/markets/metals/precious/e-micro-gold.contractSpecs.html). Do not confuse MGC with spot XAU/USD or cash-settled smaller gold products.

The strategy's sessions are trader-defined entry windows. Exchange availability and holidays are separate inputs; [CME's trading-hours calendar](https://www.cmegroup.com/trading-hours.html) remains the exchange source. The application can use [Massive's schedule events](https://massive.com/docs/rest/futures/schedules) for actual product open/close intervals. Standard weekday assumptions alone are insufficient around holidays.

[Massive's futures announcement](https://massive.com/blog/futures-data-has-arrived) confirms the relevant asset class is supported. Its [intraday futures guide](https://massive.com/knowledge-base/article/how-do-i-get-intraday-bars-for-a-futures-contract) explains the distinction between a product root and a dated contract. The connector excludes spread symbols, uses point-in-time contract discovery, and never assumes the first ticker alphabetically is the active trading contract. Liquidity-based front-month selection remains an operator decision.

The historical connector follows [Massive aggregate bar documentation](https://massive.com/docs/rest/futures/aggregates): requested minute resolution, start-stamped windows, pagination, and no bars for empty trade intervals. Actual responses verified nanosecond start timestamps. Its [WebSocket trade schema](https://massive.com/docs/websocket/futures/trades) provides millisecond timestamps and per-symbol sequence identifiers. The connector keeps these clocks separate and uses only completed bars for signal formation.

The user's key was tested without exposing it. REST succeeded for MNQZ6, MESZ6, and MGCZ6. WebSocket authentication returned a plan-access refusal. During an open-market check at about 05:57 UTC on 5 November, the latest returned minute began at 21:57 UTC on 4 November: approximately eight hours behind. This is an observed entitlement/recency limitation, not a general statement about every Massive subscription. No plan changes were made.

Precise fills require finer data. [TradingView's explanation of Bar Magnifier](https://www.tradingview.com/support/solutions/43000669285-what-is-bar-magnifier-backtesting-mode/) illustrates why OHLC path assumptions change simulated results. Our minute simulation states its penalties explicitly rather than assigning a favorable intrabar path.

For a future broker integration, [Interactive Brokers' bracket-order documentation](https://www.interactivebrokers.com/docs/tws-api/doc/orders/place-order) describes transmitting attached orders. That was reviewed to understand the gap between a simulated bracket and broker-native protection. No IBKR adapter or any other real-money broker execution is included; a production integration would additionally need fill/partial-fill reconciliation, order acknowledgments/rejections, reconnect recovery, account/margin checks, and tested native protective orders.

The transcripts' auction narrative is treated as a trading heuristic. A sweep does not compel price to visit the opposite side; candles do not reveal the complete resting order book, and every completed trade already matches a buyer with a seller. No external source was used to convert this narrative into a guaranteed market mechanism or to validate the marketing win-rate claims.
