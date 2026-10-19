# 0024. Lift the long-only and top-1,000 limits: shorts in research, backtests and paper; universe size per hypothesis

**Status:** Accepted (owner decisions 2026-10-19, #1863; recorded by the owner's merge of its PR)  ·  **Date:** 2026-10-19  ·  **Issue:** #1863  ·  **Supersedes, in part:** the [charter](../charter.md)'s Scope line "long-only"; [ADR 0010](0010-phase-4-risk-rules.md)'s "no shorts, ever" (point 1, the sell-quantity row's scope reason), except for live; [ADR 0015](0015-expansion-seams.md) part A's sentence that the Scope line changes only per capability at build time, and its "ADR 0010's 'no shorts, ever' holds" (for shorting only); [ADR 0014](0014-strategies-as-registered-objects.md)'s "no signed weights until an ADR allows long/short" (this is that ADR; the type change is build work); [ADR 0021](0021-alpaca-assets-borrow-status-record.md)'s "Shorting stays closed"; [ADR 0006](0006-universe-and-cadence.md) rule 8's top-1,000 as a scope limit (the default and guard (a) stand)  ·  **Related:** [ADR 0005](0005-objective-benchmark-stop-criteria.md), [ADR 0023](0023-break-first-fast-books-paper-exam.md), [#813](https://github.com/josejuarez96/tradepartner/issues/813), [beyond long-only](../research/2026-10-10-beyond-long-only.md), [strategy discovery](../research/2026-10-19-strategy-discovery.md), [daily data readiness](../research/2026-10-18-daily-data-readiness.md)

## Context

The owner, 2026-10-19: "lets actually go ahead and remove that long only decision. it keeps coming back when it doesnt need to. i think we have discussed that concentrating on long only is not a sensible route even with the most research behind it. there are strategies at every time frame not just long. thats what the tool is set to do." And, the same day: "and also this goes hand in hand with keeping our store only to top 1000."

This is the owner's direction, not a research finding. On 2026-10-10 he had said "we need to stop with long only" ([#836](https://github.com/josejuarez96/tradepartner/issues/836#issuecomment-6100220203)). The charter and ADRs 0010 and 0015 still said long-only, so every plan and kill test ran into it again. The evidence is mixed. The [beyond long-only report](../research/2026-10-10-beyond-long-only.md) grades a short leg as a source of net edge in our universe NOT SUPPORTED (LS-3): net of borrow fees, the average long-short return is about zero. Its LS-20 (short legs carry most anomaly profit) is graded INSUFFICIENT. The [strategy-discovery report](../research/2026-10-19-strategy-discovery.md) reads the long-only top-1,000 limit, not a lack of ideas, as the likely binding constraint if B10 and Q1 die.

The store already goes well beyond 1,000 names (read-only check, 2026-10-19): `prices_daily` has bars for 5,573 securities on 2026-10-16 (8,852 ever), facts cover 14,112 securities, listings 14,867, but `corporate_actions` covers only 4,820. What is top-1,000 is ADR 0006 rule 8's default `universe.top_n_by_cap = 1000`, frozen per hypothesis, and the reach of data-quality work: the #969 stop rule's answer key is S&P-member based, and health checks and repairs focused there. Live money is a taxable cash account ([#813](https://github.com/josejuarez96/tradepartner/issues/813)), and a cash account cannot short.

## Options considered

1. **Keep both limits until a build is specified** (ADR 0015 part A). Pro: no scope change before the code exists. Con: these are the rules that "keep coming back", and they shut out short legs and smaller names before any test.
2. **Lift them in research and backtests only.** Pro: no order-path risk. Con: a short idea could never become a forward exam, which is how ADR 0023 judges a strategy.
3. **Lift both for research, backtests and paper, and keep live long-only until the live account type is decided** (this record). Pro: matches the owner's words and the cash account. Con: the requirements below must be met first.

## Decision

**1. Long-only is no longer a scope constraint. Research and backtests may hold short and long/short positions at any time frame the data supports, and paper books may short on the Alpaca paper account. Live trading stays long-only on the taxable cash account (#813) until a separate decision on the live account type; this record does not allow a live short order.**

**2. The top 1,000 is no longer a scope limit. Universe size and ranking are per-hypothesis parameters: any registered hypothesis may choose a wider universe. The store and its data-quality work are not limited to the top 1,000.** ADR 0006 guard (a) stands: the universe is frozen at registration, and changing it after a first run is a new hypothesis. A family's fixed `universe.*` values still bind its own registrations. The price and liquidity floors (`universe.min_price`, `universe.min_median_dollar_volume`) stay the defaults, because they keep out the uninvestable microcap tail. A hypothesis that wants lower floors must justify them at admission.

The charter's other Scope words are unchanged: daily or slower (the "any time frame" in point 1 stays within it; intraday still needs its own ADR, per ADR 0015), no leverage and no options. In code, the wrapper's `refused_position_side` refusal and ADR 0010's structural sell-quantity rule stay as they are until the shorting build lifts them for paper.

## Consequences

**Before the first short order (paper), a later spec and plan must provide the following.** Items marked (ADR) change a recorded rule, so they also need an ADR or a dated amendment, not only a spec. These are requirements, not designs.
1. Signed positions in the backtest engine (today `_Book` in `backtest/engine.py` is unsigned) and signed strategy output. Per ADR 0023, this engine work is built for an idea that survives the cheap rungs; B12 is the report's first such test and needs no engine.
2. Short-side costs in the cost model: borrow fees, hard-to-borrow names and locate.
3. Borrow data. ADR 0021's record (T171d; ADR 0021 is still Proposed) starts on 2026-10-11, and ADR 0023 D5 paused T171d, so the owner may need to un-pause it. A backtest before that date uses a borrow-cost input registered in advance (the report's 0 / 30 / 300 bp grid), never free shorting.
4. (ADR) Risk rules for shorts, all from config: gross and net exposure, a per-name short limit, squeeze and halt handling, and the kill switch. ADR 0010 point 1's cash rule must be restated for a book with a short leg, and the owner must decide how "no leverage" applies when gross exposure is above 1.0. ADR 0010 point 5 sends new limits to an ADR.
5. Everything in ADR 0015 part C's shorting row: the margin model, short lots in `_close_fifo`, the side token in `client_order_id` and `next_attempt`'s key, `orders.phase`, the negative dividend due in reconciliation, and seam 5's `portfolio` keys. Also: how a short is closed when its stock is delisted.
6. The wrapper's `refused_position_side` refusal is lifted only by that build, and only for paper. A pinned test must show that a short is still refused on a live account.
7. (ADR) A benchmark for a book with a short leg. ADR 0005 names SPY and MTUM; the report uses T-bills for a neutral book.
8. A tax note for short sales (holding period, payments in lieu of dividends) before any live short, as in ADR 0015 part C's tax row.

**Before a hypothesis runs on a wider universe:** the data-quality checks beyond large caps have no S&P answer key (#1598's whole-universe known-answer check is the existing candidate). `corporate_actions` coverage (4,820 securities) must be checked for that universe. Costs matter more in smaller names; the [readiness report](../research/2026-10-18-daily-data-readiness.md)'s half-spread gap applies first there. The #969 stop rule's measurement stays as written, for the scope it covers.

**Unchanged:** ADR 0023's kill ladder applies to both legs. A short or long/short idea gets the same rungs, and rung 2 reads each leg's sign and size from the same open or published data. The holdout and trial-registry rules apply. So does ADR 0015 seam 5's frozen-key rule: a short or wider-universe variant is a new hypothesis identity, and H1's fingerprint does not move.

**Research:** long/short and short-leg ideas (the report's LS items and B12) and wider-universe ideas go back into the candidate pool, still through the kill ladder. B14 keeps its reconsider trigger. The owner's 10-10 "beyond long-only" decision is now fully in force.

- Good: the two limits stop coming back, and research can test short legs and smaller names directly.
- Bad / accepted risks: shorting brings unbounded loss on a name, borrow recalls and squeezes. Smaller names bring thinner data checks and higher costs. These requirements must be met before paper meets either.
- Reversibility: cheap until the shorting build, because no short order can be placed before it. After it, reversing means covering open paper shorts and retiring the signed engine. The universe change is per hypothesis and costs nothing to reverse.
- Revisit if: the live account type is decided (that decision says whether live may short). Revisit too if the shorting spec finds that Alpaca paper cannot short what the research needs. Unverified broker fact: Alpaca documents a $2,000 minimum equity for margin and short selling. If that holds, a live short also needs a charter amendment to the roughly $100 live capital.
