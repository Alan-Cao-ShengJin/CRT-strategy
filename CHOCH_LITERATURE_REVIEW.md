# How other traders use CHoCH, and what the research supports

Reviewed **6 November 2026**. This is a targeted literature review, not an exhaustive systematic review or a replication of external results. Primary practitioner publications, author/institutional repositories and publisher records are used. Access limitations and working-paper versions are identified below. The structured source register is [research/choch_literature_sources.json](research/choch_literature_sources.json).

**The useful finding is that CHoCH is used as one component of several different trading processes. Our current five-minute retest rules cover only one interpretation. I did not find strong, independently replicated, peer-reviewed evidence establishing consistent net profitability for a fixed five-minute CHoCH system on MNQ or MCL. That is a bounded search finding, not proof that every possible CHoCH approach fails.**

## What the practitioner sources describe

These sources document how their authors teach the concepts. They do not establish audited profitability or how frequently the wider trading population uses each approach. Brokers, platform vendors and educators have commercial interests.

| Approach | What the trader waits for | Role of CHoCH | Difference from our current system | Primary practitioner source |
|---|---|---|---|---|
| Higher-timeframe context | A directional view from a slower chart before evaluating a faster structural shift | A contextual trigger rather than an automatic reversal of the entire market | Our CHoCH engine does not require a higher-timeframe directional filter | [FXOpen: MSS](https://fxopen.com/blog/en/market-structure-shift-meaning-and-use-in-ict-trading/) |
| Sweep, displacement and zone retest | A liquidity-level interaction, forceful structural break and a return to an identified zone | One stage of a multi-condition setup | We require a retest of the broken pivot; we do not require a sweep, displacement, fair-value gap or order-block entry | [FXOpen: MSS](https://fxopen.com/blog/en/market-structure-shift-meaning-and-use-in-ict-trading/), [ATAS: CHoCH](https://atas.net/blog/understanding-change-of-character-choch-in-trading/) |
| Follow-through confirmation | Further structure in the new direction after the initial warning | Early warning, followed by a later confirmation such as BOS | We can enter after the initial break/retest without a subsequent continuation break | [FXOpen: MSS](https://fxopen.com/blog/en/market-structure-shift-meaning-and-use-in-ict-trading/) |
| Volume or footprint confirmation | How price responds to activity around the level, including delta/volume examples | A prompt to inspect the move more closely | Our data contain minute OHLCV; the baseline entry does not require volume confirmation and has no genuine order-flow input | [ATAS: CHoCH](https://atas.net/blog/understanding-change-of-character-choch-in-trading/) |
| Wyckoff-style interpretation | Changes in price spread, volume, reactions and the surrounding trading range | A broader change in price/volume behavior | This is not identical to our strict pivot-break rule | [Wyckoff Analytics: method](https://www.wyckoffanalytics.com/wyckoff-method/) |

The vocabulary needs to be frozen before testing. In FXOpen's terminology CHoCH can precede MSS, with stronger momentum confirmation distinguishing the latter. ATAS and Wyckoff material organize the concepts differently. Treating all of those labels as the same executable strategy would make the comparison ambiguous. This is a comparison of the reviewed definitions, not an assertion that any provider owns a canonical definition.

An illustrative multi-timeframe idea is a bullish slower-chart context, a bearish five-minute pullback, then a bullish five-minute structural change. That is our proposed mechanical interpretation of contextual trading; the exact timeframes and thresholds are not established by the cited research.

## The most directly relevant research posting found

**Shepherd Mahadzva (2026), [Smart Money or Costly Folklore? A Systematic Evaluation of ICT/Smart-Money-Concepts Trading Rules in G10 FX](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7430998).** SSRN record: written 1 July 2026, posted 14 September 2026.

The indexed abstract reports tests of six rule families on EURUSD, GBPUSD, AUDUSD and USDJPY, including CHoCH with fair-value-gap retracement. It reports that several textbook directional interpretations did not survive its tests, with some reversed interpretations performing better.

**Access and evidence limit:** the primary SSRN record was visible in indexed search, but direct page/PDF retrieval returned HTTP 403. This review therefore uses its indexed abstract only. Peer review was not verified; its full methods, code, costs, selection process and results were not independently audited. It does not establish an edge on micro futures. It is a lead for further reading, not sufficient evidence to reverse our signals or import its reported returns.

## Research papers worth reading

Most of the established literature studies related mechanisms or general technical rules, rather than the modern retail CHoCH label. The distinction matters: a price pattern can contain information without being a profitable executable strategy after costs.

| Paper | Evidence and scope | What it contributes to this project | What it does not establish / access status |
|---|---|---|---|
| **Lo, Mamaysky & Wang (2000), Foundations of Technical Analysis**, *Journal of Finance* | Systematic chart-pattern recognition found incremental information in some daily US-stock return distributions | Shows that subjective patterns can be formalized and tested | Not a five-minute CHoCH test or proof of net trading profits. Author's [publication record and abstract](https://alo.mit.edu/publications/page/19/) inspected; full-paper retrieval was unsuccessful |
| **Osler (2000), Support for Resistance: Technical Analysis and Intraday Exchange Rates**, New York Fed *Economic Policy Review* | Examined currency support/resistance levels distributed by six firms and found predictive content for intraday interruptions, varying by market and provider | Supports testing the identity and context of a level rather than treating every swing as interchangeable | Not evidence that every broken level should be faded, or that an MCL/MNQ strategy is profitable. [Institutional article and full text](https://www.newyorkfed.org/research/epr/00v06n2/0007osle.html) accessed |
| **Osler (2005; working paper 2002), Stop-Loss Orders and Price Cascades in Currency Markets**, *Journal of International Money and Finance* | Evidence links clustered currency stop orders to self-reinforcing moves | A stop-level crossing can accelerate continuation; a reversal requires separate evidence | Does not identify institutional intent from a candle or validate a sweep-reversal strategy. [New York Fed record](https://www.newyorkfed.org/research/staff_reports/sr150.html) includes the published citation; institutional summary inspected |
| **Chung & Bellotti (2021), Evidence and Behaviour of Support and Resistance Levels in Financial Time Series**, arXiv | Their intraday level-detection study found that bounce behavior depends on previous interactions and elapsed time | Suggests explicitly recording level age and previous touches as research features | Does not demonstrate a costed CHoCH trading strategy. [Preprint and full text](https://arxiv.org/abs/2101.07410) accessed; journal publication was not verified |
| **Cont, Kukanov & Stoikov (2014), The Price Impact of Order Book Events**, *Journal of Financial Econometrics* | For 50 US stocks, order-flow imbalance at the best quotes related more robustly to short-horizon price changes than aggregate traded volume | Explains why genuine order-flow evidence differs from a large-volume candle | Not evidence that a retail footprint filter earns profits on futures. [Author-submitted abstract and publication reference](https://arxiv.org/abs/1011.6402) inspected |
| **Sullivan, Timmermann & White (1999), Data-Snooping, Technical Trading Rule Performance, and the Bootstrap**, *Journal of Finance* | Evaluates rule searches with an adjustment for testing many alternatives; historical success did not carry into its later experiment | We must evaluate the entire rule search, not only whichever filter wins | It does not say that every historical technical result disappears after adjustment. [LSE working-paper predecessor](https://eprints.lse.ac.uk/119144/1/dp303.pdf) inspected; [published record](https://doi.org/10.1111/0022-1082.00163) confirmed |
| **Bajgrowicz & Scaillet (2012), Technical Trading Revisited**, *Journal of Financial Economics* | Studies false discoveries, persistence and costs; historical rule rankings did not provide a dependable route to selecting future winners in its experiment | Strong reason to distrust selection of a winning asset/session/filter from a large retrospective search | It is not a universal rejection of intraday futures trading. [Published record](https://doi.org/10.1016/j.jfineco.2012.06.001) and indexed abstract located; an accessible [author-deposited 2009 draft](https://archive-ouverte.unige.ch/unige:5724) inspected, not the full final 2012 text |
| **Park & Irwin (2007), What Do We Know About the Profitability of Technical Analysis?**, *Journal of Economic Surveys* | Review classified 95 modern studies as 56 positive, 20 negative and 19 mixed, while identifying substantial testing limitations | The literature is mixed; neither blanket enthusiasm nor blanket dismissal is justified | Those counts are studies, not the probability that our strategy succeeds. [University publication record and abstract](https://experts.illinois.edu/en/publications/what-do-we-know-about-the-profitability-of-technical-analysis/) inspected; full final text not accessed |

## What this changes about interpreting our backtest

Our implementation in [research/choch.py](research/choch.py) uses strict two-left/two-right confirmed pivots, a trend inferred from the latest two highs and lows, an opposite closing break, a structural stop, a confirmed retest within three five-minute candles and a 2R target. It also has session, missing-data, risk and daily-flatten rules. Its entry logic does not require the extra contextual conditions listed above. Enabling a generic configuration flag would not by itself implement a correctly timed higher-timeframe CHoCH filter.

The [additional-history validation](CHOCH_FIVE_MINUTE_VALIDATION.md) remains the relevant evidence for those exact rules: MCL lost 7.96% and MNQ lost 7.77% on the added April-October 2025 $50,000 cases after modeled costs, although their extended accounts ended positive. Descriptions of richer practitioner setups do not invalidate those results or prove that adding conditions will repair them.

There are two distinct questions for subsequent work:

1. Does the structural event predict a different return distribution than comparable non-event times?
2. Can a specified entry, stop, target and sizing rule turn that difference into net returns under executable costs and capital limits?

The first result alone is insufficient. Likewise, neither a candle gap nor a price move through a previous high reveals who traded or proves deliberate stop hunting. Our minute aggregate data cannot establish true order-book imbalance, absorption or aggressor delta. Those ideas require appropriate trade/quote data and an explicit measurement method; ordinary volume must not be relabelled as those measurements.

## A bounded research agenda

These are **proposed experiments**, not completed backtests, validated improvements or rules selected for deployment. The ranking below is my assessment of relevance and testability, not a ranking established by a paper.

| Priority | Hypothesis | How to make a fair experiment | Important failure mode |
|---|---|---|---|
| 1 | Slower-chart direction helps a five-minute CHoCH time a pullback | Freeze one causal higher-timeframe definition; compare the unchanged baseline with one aligned variant | Using an unfinished higher-timeframe candle, or choosing the best timeframe after viewing returns |
| 2 | Momentum or subsequent BOS distinguishes a reversal from a small break | Test displacement and follow-through separately, each against the same baseline | Optimizing a large collection of ATR/body thresholds or accidentally using later swing confirmations |
| 3 | Rejected breaks and accepted breaks behave differently | Define a prior known level and an observable reclaim/hold rule; test reversal and continuation as distinct hypotheses | Labelling every penetration a reversal, despite the evidence for stop-driven continuation |
| 4 | The economic edge is too small for the trade's costs | Re-run sizing and execution with a declared minimum planned-stop/cost relationship | Raising reported win rate while silently removing most opportunities or changing capital exposure |
| Later | Order-flow confirmation improves selection | First establish actual data coverage, signing rules and incremental feed/execution costs | Inventing delta/absorption from OHLCV or treating contemporaneous price impact as a forecast |

For every new variant: preserve the baseline; change one condition at a time; use only information available at the decision time; disclose every tested setting; report net R, account returns, drawdown, trade count and opportunity loss; and include matched control events or a suitable null model. A control must preserve relevant session, volatility and dependence structure rather than comparing with arbitrary clock times.

Our existing historical samples have already influenced strategy and market choices. A chronological split of those same samples is useful retrospective analysis, but cannot become untouched validation again. New rules need a frozen design and genuinely subsequent paper observations, or other defensible data not used to choose them. Multiple-testing adjustments and dependence-aware uncertainty estimates help; they do not substitute for those design choices.

The MCL New York short weakness found in our previous report is a separate post-hoc diagnostic. Literature about confluence does not turn that observation into an independently validated short filter. It should remain identified as a hypothesis.

The next useful comparison is an unchanged five-minute baseline against a clearly specified higher-timeframe-context variant, followed separately by a displacement or follow-through variant. This review supplies reasons to run those tests; it supplies no new profitability claim.
