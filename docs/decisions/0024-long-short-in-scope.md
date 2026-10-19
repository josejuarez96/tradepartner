# 0024. Long-only is no longer a scope constraint: shorts in research, backtests and paper

**Status:** Accepted (owner decision 2026-10-19, #1863; recorded by the owner's merge of its PR)  ·  **Date:** 2026-10-19  ·  **Issue:** #1863  ·  **Supersedes:** [ADR 0010](0010-phase-4-risk-rules.md)'s "no shorts, ever" (point 1, the sell-quantity row's scope reason) except for live; the [charter](../charter.md)'s Scope line "long-only"; [ADR 0015](0015-expansion-seams.md) part A's sentence that the Scope line changes only per capability at build time, for shorting only  ·  **Related:** [ADR 0005](0005-objective-benchmark-stop-criteria.md), [ADR 0014](0014-strategies-as-registered-objects.md), [ADR 0021](0021-alpaca-assets-borrow-status-record.md), [ADR 0023](0023-break-first-fast-books-paper-exam.md), [#813](https://github.com/josejuarez96/tradepartner/issues/813), [beyond long-only](../research/2026-10-10-beyond-long-only.md), [strategy discovery](../research/2026-10-19-strategy-discovery.md)

## Context

The owner, 2026-10-19: "lets actually go ahead and remove that long only decision. it keeps coming back when it doesnt need to. i think we have discussed that concentrating on long only is not a sensible route even with the most research behind it. there are strategies at every time frame not just long. thats what the tool is set to do."

On 2026-10-10 he had said "we need to stop with long only" ([#836](https://github.com/josejuarez96/tradepartner/issues/836#issuecomment-6100220203)), and the [beyond long-only report](../research/2026-10-10-beyond-long-only.md) followed. Yet the charter, ADR 0010 and ADR 0015 still said long-only, so every plan and kill test kept running into it. The [strategy-discovery report](../research/2026-10-19-strategy-discovery.md) adds that if B10 and Q1 die, the evidence points at the long-only top-1,000 constraint, not at a lack of ideas, because anomaly profits sit mostly in short legs (LS-20). Live money is a taxable cash account ([#813](https://github.com/josejuarez96/tradepartner/issues/813)), and a cash account cannot short.

## Options considered

1. **Keep long-only until a shorting build is specified** (ADR 0015 part A). Pro: no scope change before the code exists. Con: this is the rule that "keeps coming back", and it shuts short legs out of research even though that is where the evidence says the edge is.
2. **Long/short in research and backtests only.** Pro: no order-path risk. Con: a short idea could never become a forward exam, which is how ADR 0023 judges a strategy.
3. **Lift it for research, backtests and paper, keep live long-only until the live account type is decided** (this record). Pro: matches the owner's words and the cash account. Con: requirements must be met before the first short order (below).

## Decision

**Long-only is no longer a scope constraint. Research and backtests may hold short and long/short positions at any time frame the data supports, and paper books may short on the Alpaca paper account. Live trading stays long-only on the taxable cash account (#813) until a separate decision on the live account type; this record does not allow a live short order.**

The charter's Scope line now reads "long and short" with that live limit. Its other words (daily or slower, no leverage, no options) are unchanged here. In code, the wrapper's `refused_position_side` refusal and ADR 0010's structural sell-quantity rule stay as they are until the shorting build lifts them for paper.

## Consequences

**Before the first short order, a later spec and plan must provide** (these are requirements, not designs):
1. Signed positions in the backtest engine. Today `_Book` (`backtest/engine.py`) is unsigned. Strategy output must be signed too: ADR 0014 allows no signed weights until an ADR allows long/short, and this ADR does.
2. Short-side costs in the cost model: borrow fees, hard-to-borrow names and locate.
3. Point-in-time borrow availability data: ADR 0021's record (T171d), which ADR 0023 D5 paused. The owner may need to un-pause it.
4. Risk rules for shorts, all from config: gross and net exposure, a per-name short limit, squeeze and halt handling, and the kill switch. Also ADR 0010 point 1's cash rule restated for a book with a short leg. How "no leverage" applies to a book whose gross exposure is above 1.0 is for that spec to propose and the owner to decide.
5. The wrapper's `refused_position_side` refusal is lifted only by that build, and only for paper.
6. A benchmark for a book with a short leg (ADR 0005 names SPY and MTUM; the report uses T-bills for a neutral book).
7. A tax note for short sales (holding period, payments in lieu of dividends) before any live short, per ADR 0015 part C's tax row.

**Unchanged:** ADR 0023's kill ladder applies to both legs: a short or long/short idea gets the same rungs, with each leg's sign and size read where the long leg's is today. The holdout and trial-registry rules apply. So does ADR 0015 seam 5's frozen-key rule (a short variant is a new hypothesis identity, and H1's fingerprint does not move).

**Research:** long/short and short-leg ideas (the report's LS items, B12-style decompositions and hedges, the B14 overlay) go back into the candidate pool. They still go through the kill ladder. The owner's 10-10 "beyond long-only" decision is now fully in force.

- Good: the constraint stops coming back, and research can test where the evidence says the edge is.
- Bad / accepted risks: shorting brings unlimited loss on a name, borrow recalls and squeezes; items 1 to 5 must be in place before paper meets them.
- Reversibility: cheap. Until the build, no short order can be placed, and live stays long-only.
- Revisit if: the live account type is decided (that decision says whether live may short), or the shorting spec finds Alpaca paper cannot short what the research needs.
