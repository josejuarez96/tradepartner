# Hypothesis Backlog

**Status:** living, first draft 2026-10-03 (#620)  ·  **Order set by:** the owner (this draft is a proposal)  ·  **Rules:** [research-program.md](../ways-of-working/research-program.md)  ·  **Claims:** ids refer to [claims.toml](claims.toml)

What TradePartner should test next, and why, in proposed order. An entry here is **not** a registered hypothesis: promotion needs the five conditions in [research-program.md §4](../ways-of-working/research-program.md#4-promotion-backlog--hypothesis-file). Ranking is by **information value per unit of cost**, not by expected return. The priors are deliberately low: published effects lose about half their return after publication (QI-2), and post-2005 non-microcap anomaly returns are near zero (G2-S10).

**What the tool can run today:** one family, `momentum` (config `hypotheses.families`). Every other item needs a new signal family (engine work), and most need new data. The store holds prices from 2016 (ADR 0009), the filing index with UTC acceptance times, and one company fact (`EntityCommonStockSharesOutstanding`, `ingest.py` `FACT_NAMES`). It holds no income-statement facts, no filing text and no Form 4 transactions.

## Summary

| # | Item | Claims it tests (grade) | Data | Engine | Cost | Status |
|---|---|---|---|---|---|---|
| B1 | 12-1 momentum, long-only, monthly (H1) | HO-1 SUPPORTED vs G1-1, QI-3 MIXED | have | have | — | hypothesis file on `main`; `hypothesis register` and the run are the owner's T45b |
| B2 | Trend filter on H1 (hold cash when the market is below its 10-month average) | TT-1, HO-5 MIXED | have | small | S | **parked** (#659) |
| B3 | Profitability tilt (gross profit / assets), slow, long-only | QI-6 SUPPORTED (narrow); QI-16 MIXED | **5 XBRL facts** (#660 spec merged; in the store with T78) | new family (spec amendment #720, draft) | M | hypothesis file drafted (#720), not registrable |
| B4 | Momentum + profitability combined | G4-2 MIXED; G4-1 SUPPORTED (long-short only) | after B1, B3 | small | S | after B1 and B3 |
| B5 | Filing change (deterministic text) → next-quarter fundamentals | ER-4, ER-5 INSUFFICIENT; INT-4 | **filing text ingest** | research regression, not the backtester | L | proposed |
| B6 | Filing change → returns (Lazy Prices, post-publication, net) | ER-1 INSUFFICIENT; NE-6 | after B5 | new family | M | after B5 |
| B7 | Insider purchases: monthly, acceptance-timed, opportunistic or clustered | G2-B MIXED; G2-A NOT SUPPORTED; NE-3, NE-4 | **Form 4 ingest** (+3 years of history) | new family | L | proposed |
| B8 | Semantic labels (demand, inventory, liquidity): annotation pilot | TX-*, AP-*; ER-14 INSUFFICIENT | after B5's corpus | none (human labeling) | M + owner spend | parallel track, owner decisions pending |

Parked ideas are listed at the end with the reason.

## Items

### B1. 12-1 momentum (H1): file written, registration waits on T45b
- **Tests:** HO-1 (SUPPORTED, handoff) against G1-1 and QI-3 (MIXED under a modern, net, post-2010 standard). The conflict is recorded in both claims.
- **Prior:** near zero excess over MTUM and SPY. H1 is mainly a **systems test**: engine, costs, holdout, registry.
- **Kill / outcome:** as written in [h1-momentum-12-1](../hypotheses/h1-momentum-12-1.md). Not registered yet: `hypothesis register` runs on the owner's store in T45b.
- **Information value:** the engine is proven end to end, and every later item depends on that. Regrades G1-1 with our own data (a `TP-` claim).

### B2. Trend filter overlay on H1 (parked)
- **Parked 2026-10-03 (owner decision, #659):** a higher Sharpe at a lower return is not an acceptable objective under [ADR 0005](../decisions/0005-objective-benchmark-stop-criteria.md), whose objective is return against the benchmark, and the [trend-timing review](2026-09-25-trend-timing.md)'s best out-of-sample net result (a Sharpe gain of 7% or less at a lower return) leans weaker than the register's MIXED grades on TT-1 and HO-5; the decision calls it near NOT SUPPORTED. Revisit only with an ADR 0005 amendment; the design below stays for that case.
- **Tests:** TT-1 and HO-5 (MIXED: shallower crashes, but lag and whipsaw; independent tests show at best a marginal gain).
- **Data:** prices only (have).
- **Engine:** a market-regime switch to cash inside the momentum family. It fits the strategy-lab sweep design ([strategy-lab spec](../specs/strategy-lab.md), draft).
- **Prior:** no improvement in Sharpe; smaller maximum drawdown at the cost of return.
- **Kill:** no drawdown reduction that survives costs, or a lower deflated Sharpe than H1 alone.
- **Information value:** the cheapest test of whether a risk overlay belongs in the system at all. It needs no new data.
- **Cost:** S. It counts as trials in the `momentum` family, so it raises H1's deflated-Sharpe bar.

### B3. Profitability tilt: file drafted, waits on the family and T78
- **Tests:** QI-6, the only SUPPORTED stock-selection claim in the register, narrowly: slow, liquid, diversified profitability. QI-16: broader quality bundles are MIXED, so stay with plain gross profitability.
- **Data:** the five as-filed statement facts of the data-foundation amendment #660 (`revenue`, `cost_of_revenue`, `gross_profit` with a derived fallback, `total_assets`, `operating_cash_flow`): first vintage only, `known_at` = the filing's acceptance, conflicts withheld (EP-P12, EP-P13). The spec is merged; the store holds the rows once the owner runs T78. B3 reads `gross_profit` and `total_assets`; the cash-based variant reads `operating_cash_flow`.
- **Engine:** a new `profitability` family: an annual signal with a monthly rebalance, inside the ADR 0006 universe. Specified as a draft amendment of the [backtest spec](../specs/backtest.md#amendment-2026-10-03-720-the-profitability-family-draft) (#720): what the engine reads, the point-in-time rule for annual facts, the `profitability.*` keys, the counts and the proposed tasks T85 to T85f.
- **Hypothesis file:** [b3-gross-profitability](../hypotheses/b3-gross-profitability.md) (draft, #720): prior, kill, power arithmetic, prior-evidence disclosure and the family's trial budget, with eight open questions for the owner (B3-1 to B3-8). Not registrable until the family exists and T78 lands.
- **Prior:** small positive excess, well below published magnitudes (0 to +0.5 pp/yr over SPY; range −2 to +2). Expect long flat stretches and a sector-shaped book.
- **Kill:** the file's retirement rule (first audited in-sample trial with net excess over SPY below −1 pp/yr and `dsr_excess` below 0.5); the momentum-and-market spanning check is a pre-declared diagnostic, a registered research run, not part of the rule (B3-8).
- **Information value:** high. It is the best-evidenced idea, it is the first non-price signal, and it proves the fundamentals pipeline that B5 and the E1 economic test also need (revenue).
- **Cost:** M (one data extension, merged as a spec; one family, five agent tasks and one owner run).

### B4. Momentum + profitability combined
- **Tests:** G4-2 (MIXED for long-only, liquid portfolios); G4-1 (SUPPORTED for long-short or unconstrained only).
- **Prior:** a modest gain over either alone, mostly from lower turnover and diversification.
- **Kill:** no improvement over the better single signal, net, after counting the combination's trials.
- **Cost:** S once B1 and B3 exist. Combination weights are fixed in advance (equal ranks), never fitted on returns (HO-10).

### B5. Filing change → next-quarter fundamentals (deterministic)
- **Tests:** ER-4 and ER-5 (filing change tracks and predicts economic change: INSUFFICIENT, one or two sources); INT-4 (deterministic features before any model).
- **Data:** 10-Q and 10-K bodies, with MD&A and Risk Factors sections, archived with hashes. **The store has none today** (CP §4). Revenue from B3's facts extension.
- **Engine:** not a backtest. It is the regression in the [ML validation brief §8](2026-10-03-ml-validation-methodology-brief.md#8-economic-validation-stream-stage-5), with deterministic text as the treatment (TF-IDF similarity, changed and inserted sentence ratios, length change, Loughran–McDonald counts). It needs the research registry from that brief's §10.
- **Prior:** a weak relation that partly disappears once contemporaneous growth is controlled for.
- **Kill:** no incremental out-of-sample explanatory power over fundamentals alone.
- **Information value:** high. It sets the bar every semantic or model feature must beat (B8), and it is the cheaper half of the Lazy Prices question.
- **Cost:** L (corpus ingest plus a section parser), shared with B6 and B8.

### B6. Filing change → returns (post-publication Lazy Prices)
- **Tests:** ER-1 (in-sample up to 188 bp/month; INSUFFICIENT post-publication and net); NE-6.
- **Prior:** at or below the McLean–Pontiff haircut (QI-2), and likely smaller in large caps.
- **Kill:** no net excess return in the ADR 0006 universe, in-sample, at the family's deflated bar.
- **Information value:** fills the evidence class the literature lacks (post-publication, net, large caps).
- **Cost:** M after B5's corpus.

### B7. Insider purchases, monthly
- **Tests:** G2-B (MIXED; support is pre-2008 and gross only); G2-A (NOT SUPPORTED for days-horizon trading after acceptance); NE-3 and NE-4 (old, small-firm evidence).
- **Data:** a Form 4 transaction ingest (transaction code P, acquired). Transaction date is stored apart from the acceptance timestamp. The draft [event-data spec](../specs/event-data.md) covers the collector. Needs 3 years of history before the test start for the opportunistic/routine classification.
- **Design:** the ordered tests in the [ML validation brief §11](2026-10-03-ml-validation-methodology-brief.md#11-insider-purchase-stream).
- **Prior:** centered near zero.
- **Cost:** L. Rank it after B3 and B5, unless the event-data collector is built anyway for other reasons.

### B8. Semantic disclosure labels: annotation pilot
- **Not a strategy test.** It measures whether humans can label demand, inventory and liquidity direction reliably ([taxonomy v0](2026-10-03-semantic-taxonomy-v0.md), [protocol](2026-10-03-disclosure-annotation-protocol.md)).
- **Tests, later:** whether labels add to B5's deterministic features. ER-14: typed models are unvalidated on this task. ER-10: semantic beating bag-of-words is INSUFFICIENT.
- **Needs:** B5's corpus; owner decisions on annotators and budget; the research-measurement ADR before any model arm (CP-X2).
- **Cost:** M plus annotation spend (illustrative 80–180 hours).

## Parked (not recommended now)

| Idea | Why parked | Claims |
|---|---|---|
| Trend filter overlay on H1 (B2) | Owner decision 2026-10-03 (#659): a higher Sharpe at a lower return is not an acceptable objective under ADR 0005, and the best out-of-sample net result is a Sharpe gain of 7% or less at a lower return. Revisit only with an ADR 0005 amendment. | TT-1, HO-5 MIXED |
| Earnings drift (PEAD), conditional or not | Gone outside microcaps since about 2006. The surprise at announcement needs press-release parsing, and the analyst variables need paid data. | HO-4 NOT SUPPORTED; NE-1, NE-2; CP-X5 |
| Search-volume and attention signals | No post-publication net evidence in non-microcaps | G5-A NOT SUPPORTED; G5-B, HO-7 INSUFFICIENT |
| Retail-herding avoidance filter | MIXED; the data source needed is not available to us | HO-6 MIXED |
| Fund fire-sale reversal | The effect is negligible after measure correction | HO-8 MIXED |
| Index-addition trade | The effect fell from 7.4% to under 1% | QI-19 NOT SUPPORTED |
| Short-horizon reversal, stat arb | High turnover; no verified modern net edge | QI-17 MIXED |
| Low-beta, betting-against-beta | Construction challenged; may repackage profitability (B3 tests that) | QI-18 MIXED |
| Standalone value | Modern all-cost alpha unknown; long dry spells. Reconsider after B4. | QI-14 MIXED |
| LLM-generated return signals | No post-cutoff net evidence; barred by ADR 0008 | HO-9, QI-8, ER-17 INSUFFICIENT |
| Deep learning on prices, RL trading | No general evidence for standalone alpha | QI-22 INSUFFICIENT |
| Diversified futures trend | SUPPORTED, but outside a long-only equity system's scope (charter) | QI-13 SUPPORTED |

## Changing this file

- **Add an item:** cite its claim ids, prior, data, engine work, cost, kill criterion and information value. An idea with no graded claim behind it enters as "needs research" with a brief proposed.
- **Reorder:** the owner only. An agent proposes the new order and its reason in a PR.
- **Park an item:** the owner only, by a linked decision. The item keeps its section, marked parked with the reason first, and gains a row in the Parked table.
- **Close an item:** link the `TP-` claim that records the result. Retired items stay in the file, struck through, with that link.
