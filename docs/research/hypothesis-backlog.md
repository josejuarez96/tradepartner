# Hypothesis Backlog

**Status:** living, first draft 2026-10-03 (#620)  ·  **Order set by:** the owner (this draft is a proposal)  ·  **Rules:** [research-program.md](../ways-of-working/research-program.md)  ·  **Claims:** ids refer to [claims.toml](claims.toml)

What TradePartner should test next, and why, in proposed order. An entry here is **not** a registered hypothesis: promotion needs the five conditions in [research-program.md §4](../ways-of-working/research-program.md#4-promotion-backlog--hypothesis-file). Ranking is by **information value per unit of cost**, not by expected return. The priors are deliberately low: published effects lose about half their return after publication (QI-2), and post-2005 non-microcap anomaly returns are near zero (G2-S10).

**What the tool can run today (2026-10-09):** three engine-ready families, `momentum`, `profitability` and `combined` (`config.FAMILIES`; `oracle` is test-only), of which `momentum` is paper-ready and the other two become so under ADR 0017 (paper-trading plan T152). Every other item needs a new signal family or a family amendment (engine work), and most need new data. The store holds prices from 2016 (ADR 0009), the filing index with UTC acceptance times, the shares-outstanding fact (`EntityCommonStockSharesOutstanding`) and, since T78, the five as-filed statement facts of #660. It holds no 8-K `items` (B9's need, data-foundation plan T164 to T164e), no filing text and no Form 4 transactions.

## Summary

| # | Item | Claims it tests (grade) | Data | Engine | Cost | Status |
|---|---|---|---|---|---|---|
| B1 | 12-1 momentum, long-only, monthly (H1) | HO-1 SUPPORTED vs G1-1, QI-3 MIXED | have | have | — | hypothesis file on `main`; `hypothesis register` and the run are the owner's T45b |
| B2 | Trend filter on H1 (hold cash when the market is below its 10-month average) | TT-1, HO-5 MIXED | have | small | S | **parked** (#659) |
| B3 | Profitability tilt (gross profit / assets), slow, long-only | QI-6 SUPPORTED (narrow); QI-16 MIXED | **5 XBRL facts** (#660 spec merged; T76, T76b built; in the store with T78) | new family (spec amendment #720, accepted 2026-10-06; plan tasks T85 to T85f) | M | file drafted and amended from the coverage spike (#1033); registers at T85f, after T85e and T78 |
| B3b | Cash profitability tilt (operating cash flow / assets), slow, long-only | QI-6 SUPPORTED (narrow), as a proxy; the accrual-adjusted measure is ungraded | have once T78 lands (`operating_cash_flow`, #660) | `basis = "cash"` in the `profitability` family (one literal value) | S | candidate follow-up (owner, 2026-10-06, #720); after B3 has run |
| B4 | Momentum + profitability combined | G4-2 MIXED; G4-1 SUPPORTED (long-short only) | after B1, B3 | small | S | after B1 and B3 |
| B5 | Filing change (deterministic text) → next-quarter fundamentals | ER-4, ER-5 INSUFFICIENT; INT-4 | **filing text ingest** | research regression, not the backtester | L | proposed |
| B6 | Filing change → returns (Lazy Prices, post-publication, net) | ER-1 INSUFFICIENT; NE-6 | after B5 | new family | M | after B5 |
| B7 | Insider purchases: monthly, acceptance-timed, opportunistic or clustered | G2-B MIXED; G2-A NOT SUPPORTED; NE-3, NE-4 | **Form 4 ingest** (+3 years of history) | new family | L | proposed |
| B8 | Semantic labels (demand, inventory, liquidity): annotation pilot | TX-*, AP-*; ER-14 INSUFFICIENT | after B5's corpus | none (human labeling) | M + owner spend | parallel track, owner decisions pending |
| B9 | Earnings-reaction (EAR) drift: top quintile by the [−1, +2] market-adjusted return around the 8-K Item 2.02 acceptance, 20-session hold, daily | SH-6 MIXED; SH-12 (a T1-source fact); against SH-5 NOT SUPPORTED, SH-2 | **8-K `items` field** (data-foundation plan T164 to T164e); bars, actions, SPY held | new root family `earnings_drift` (spec amendment, then the tasks listed in the file's "The family question") | M | file drafted (#1358); forward holdout, judged by its paper book; needs ADR 0017 for the book |
| B10 | Short-term momentum (STMOM): last month's winners in the top turnover quintile, monthly | SH-9 INSUFFICIENT; against SH-1 NOT SUPPORTED, SH-2 | have (volume, shares fact, actions) | `momentum` family amendment: one key `strategy.turnover_top_fraction` | S | file drafted (#1358); no exam of record (family holdout spent); paper book at the owner's word |

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

### B3. Profitability tilt: family amendment accepted, waits on T85 to T85e and T78
- **Tests:** QI-6, the only SUPPORTED stock-selection claim in the register, narrowly: slow, liquid, diversified profitability. QI-16: broader quality bundles are MIXED, so stay with plain gross profitability.
- **Data:** the five as-filed statement facts of the data-foundation amendment #660 (`revenue`, `cost_of_revenue`, `gross_profit` with a derived fallback, `total_assets`, `operating_cash_flow`): first vintage only, `known_at` = the filing's acceptance, conflicts withheld (EP-P12, EP-P13). The spec is merged; the store holds the rows once the owner runs T78. B3 reads `gross_profit` and `total_assets`; the cash-based variant reads `operating_cash_flow`.
- **Engine:** a new `profitability` family: an annual signal with a monthly rebalance, inside the ADR 0006 universe. Specified by the [backtest spec](../specs/backtest.md#amendment-2026-10-03-720-the-profitability-family-accepted-2026-10-06)'s amendment #720, **accepted 2026-10-06** (owner decisions on #720, streamlined by #1033): what the engine reads, the point-in-time rule for annual facts, the `profitability.*` keys, the counts; its tasks T85 to T85f are in [docs/plans/backtest.md](../plans/backtest.md).
- **Coverage (read-only spike, 2026-10-06, `~/tradepartner-probes/b3-coverage/`):** at every month-end 2020-08 to 2023-12, about 610 of about 795 in-scope names score (about 1,008 in the universe, about 212 out as financials), a portfolio of 58 to 62; 0 of 26,676 fact pairs accepted after the close; about 28% of scores derived. The unscored are a systematic slice (no cost-of-revenue line: payments networks, telecom and cable, rails and parcel, integrated oil, restaurants; abandoned gross-profit tags at TMO, ORCL, INTU, DE, RTX, TMUS, T), so B3 tests gross profitability **among firms that report a cost of revenue**, tilted away from mega-caps and from services, energy and telecom; the file discloses the tilt. Owner decisions 2026-10-06: proceed as specified; keep #660's tag list; the cash variant goes to B3b.
- **Hypothesis file:** [b3-gross-profitability](../hypotheses/b3-gross-profitability.md) (#720; amended #851, #811, #1033): prior, kill, power arithmetic, coverage and tilt, prior-evidence disclosure and the family's trial budget; every owner question B3-1 to B3-8 decided (2026-10-03 and 2026-10-04); `in_sample_start` follows H1's #975 re-pin (2020-08-31). Not registrable until T85e and T78 land (T85f).
- **Prior:** small positive excess, well below published magnitudes (0 to +0.5 pp/yr over SPY; range −2 to +2). Expect long flat stretches and a sector-shaped book.
- **Kill:** the file's retirement rule (first audited in-sample trial with net excess over SPY below −1 pp/yr and `dsr_excess` below 0.5); the momentum-and-market spanning check is a pre-declared diagnostic, a registered research run, not part of the rule (B3-8).
- **Information value:** high. It is the best-evidenced idea, it is the first non-price signal, and it proves the fundamentals pipeline that B5 and the E1 economic test also need (revenue).
- **Cost:** M (one data extension, merged as a spec; one family, five agent tasks and one owner run).
- **Follow-ups from the #726 reviews, settled (#728):** the health page's coverage bound stays `universe.max_shares_age_days`, with a note that it under-reports what a wider `profitability.max_fact_age_days` can use (data-foundation spec, "Health"); whether a `return` run may read a registry table (the spanning diagnostic's `trial_equity`) is the research-registry spec's new open question 7, left for the owner; `n_excluded_no_facts` stays one count, with the latest ingest run's per-cause counts shown beside it on the backtest page for context, not a per-rebalance split (backtest spec, T85d).

### B3b. Cash profitability tilt (candidate follow-up)
- **Why it is here:** owner decision 2026-10-06 (#720), from the coverage spike: operating cash flow over total assets is computable for about 99% of in-scope names (about 790 of about 795 per month-end), where gross profit over assets covers about 77%; it scores the names B3 cannot (V, MA, XOM, MCD, UNP) and has none of B3's abandoned-tag holes.
- **Tests:** QI-6, as a proxy for cash-based profitability; it is **not** Ball, Gerakos, Linnainmaa and Nikolaev's accrual-adjusted measure (JFE 2016; #660 decision (a) names it), which would need facts the list does not carry and would reopen data-foundation open question 8. No report grades the proxy.
- **Data:** `operating_cash_flow`, one of #660's five facts, in the store with T78. **Engine:** B3's family with `basis = "cash"` (the `Literal` gains the value by reviewed config change when the file is drafted; nothing is built now).
- **Prior:** as B3's, 0 to +0.5 pp/yr over SPY, with a smaller sector tilt. **Kill:** B3's retirement rule, read on its own first audited in-sample trial. **Cost:** S (one file, one literal value), counted in the `profitability` family's N and inside B3's budget of three pre-declared variants. Drafted only after B3 has run; it is B3's variant (i), not a new idea.

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

### B9. Earnings-reaction (EAR) drift
- **Why it is here:** rank 1 of the [short-horizon report](2026-10-09-short-horizon-candidates.md) (#1353, for the owner's direction #1352: fast paper, several books); the owner approved the hypothesis file on 2026-10-09. It is the only short-horizon candidate with independent post-publication support, and it uses the data edge the project owns: exact SEC acceptance times, here of the 8-K Item 2.02 that carries the quarterly release (SH-12).
- **Tests:** SH-6 (MIXED: the drift after the three-day announcement return persists after publication at reduced size; value-weighted long-short 0.81%/month in 2010-2024, size-screened 0.25%/month, long leg +0.07 to +0.32%/month gross; 2019-2023 alone insignificant). It must survive SH-5 (NOT SUPPORTED: surprise-based PEAD is gone outside microcaps since 2006, so only the reaction itself is a candidate) and SH-2 (costs). It is **not** the parked PEAD item: that one keys on an earnings surprise against expectations (HO-4), which needs analyst data and is gone; B9 keys on the market's own reaction, which needs only bars and the release time.
- **Data:** the EDGAR 8-K `items` field with acceptance times, which the adapter reads from the submissions payload and does not keep: [data-foundation plan](../plans/data-foundation.md) tasks **T164 to T164e** store it as `filing_events` (`known_at` = acceptance; T164 is the spec amendment and the coverage count, T164b to T164e the code, sliced by file). Bars, corporate actions and SPY (held). No vendor, intraday or analyst data.
- **Engine:** a new **root family `earnings_drift`** (strategy-lab spec open question 11 (a): an unrelated signal is a root family), by a backtest spec amendment as #720 was for `profitability`, then config, an as-of read, a pure signal (events → day 0 → window return → trailing-quarter breakpoints → live set with a 5% cap) and the paper dispatch; the hypothesis file lists the tasks with sizes. The twenty-session hold is a membership rule at the `daily` cadence (ADR 0012), so no schedule change. Under ADR 0016 its in-sample window is [2020-08-31, 2023-12-29] and its holdout is **forward** (no overlap with the spent momentum holdout; [2027-01-04, 2027-06-30] proposed), judged by its own paper book (ADR 0017, proposed).
- **Hypothesis file:** [b9-earnings-reaction-drift](../hypotheses/b9-earnings-reaction-drift.md) (#1358): prior, point-in-time rules, costs, power arithmetic, trial budget, the family question and owner questions B9-1 to B9-7. Registration enters as a one-value sweep after the lab migration (strategy-lab spec req 1).
- **Prior:** gross long leg about +0.8 to +3.8 pp/yr; one-sided turnover about 100 to 200%/month (entries and exits, their re-weighting, daily drift) costs about 3.6 to 7.6 pp/yr at 15 bp per side, so **net about zero to negative at 15 bp, positive only near 5 bp** (the report's verdict, stated plainly); net prior over SPY centred at about −2 pp/yr, range −7 to 0.
- **Kill:** the file's retirement rule (first audited in-sample trial with net excess over SPY below −1 pp/yr and `dsr_excess` below 0.5); the gross (0 bp) rung labels a retired result "cost-bound" rather than "no signal", a diagnostic, not a selection.
- **Trades:** about 65 entries and 65 exits a month, seasonal; 3 to 8 orders a session in earnings season.
- **Information value:** high per unit of cost once T164 exists: it tests whether an acceptance-timed event signal beats SPY net in our universe, proves the event path of the engine that any later 8-K keyed signal (B7's Form 4 work aside) would reuse, and gives the paper machine a daily book for the ADR 0017 shakedown.
- **Cost:** M (one data task, one spec amendment, about four engine and paper tasks, one owner registration).

### B10. Short-term momentum (STMOM)
- **Why it is here:** rank 2 of the short-horizon report (#1353); the owner accepted it on 2026-10-09 for its cheap information value and as a fast paper book, with the grade INSUFFICIENT in view.
- **Tests:** SH-9 (INSUFFICIENT: one-month winners among high-turnover large caps continue, 0.42%/month long-short gross in the largest 500 to 2018, net of half-spreads 1.00%/month full sample; no post-2018 US test found; China shows reversal only) against SH-1 (NOT SUPPORTED: the plain one-month sort has no edge in large caps) and SH-2 (costs). The pre-declared no-screen variant is the control, so one sweep tests the turnover mechanism's sign in our data.
- **Data:** held: SIP bars with volume, the shares-outstanding fact the universe's rule 7 already reads (`known_at` = acceptance), corporate actions for the split adjustment the report warns about (a mis-measured turnover "gives a reversal book").
- **Engine:** a **`momentum`-family amendment**, one new frozen key `strategy.turnover_top_fraction` (default 1.0, no screen, with a `FROZEN_KEY_DEFAULTS` entry; strategy-lab spec, Out of scope) **plus a narrow amendment of backtest spec decision 13**, without which the new key moves H1's, the T114 variants', `oracle`'s and `combined`'s fingerprints (own-section keys are kept in the canonical set even at their default; the amendment leaves out keys added after the family's first registration, never the keys a family registered with, so B3's stay), a volume-and-shares read and the screen before the rank; the hypothesis file lists the tasks with sizes. A one-month momentum sort is momentum's own signal at another horizon, so the spec's decided rule (open question 11 (a)) keeps it in the family rather than a root; the alternative (a root family with a forward holdout queued against B9's) is owner question B10-1.
- **Hypothesis file:** [b10-short-term-momentum](../hypotheses/b10-short-term-momentum.md) (#1358): H1's construction with `formation_months = 1`, `skip_months = 0`, a 0.20 turnover screen and a 0.20 position fraction (about 40 names), `month_end`; in-sample [2020-08-31, 2023-12-29]; **no exam of record** (the family's holdout is spent by H1's trial 4, and B10 asks for no repeat spend); its evidence is the in-sample trial, counted in momentum's N, and its paper book, which `momentum`'s `paper_ready` flag and ADR 0017's books allow at the owner's word.
- **Prior:** long leg unreported; about half the spread by analogy (+2.4 pp/yr gross, unverified); one-sided turnover about 90%/month costs about 3.2 pp/yr at 15 bp, so **net about zero to negative at 15 bp**; prior over SPY centred at −1 pp/yr, range −6 to +3.
- **Kill:** the file's retirement rule (as B9's); the sign against the no-screen control is a pre-declared diagnostic.
- **Trades:** about 36 entries and 36 exits a month, about 72 orders at each month-end run.
- **Information value:** moderate, at the lowest cost of any open item: no new data, one key, and the sign of the turnover-screened sort against its control regrades SH-9 and SH-1 with our data (`TP-` claims). As a paper book it is a monthly, 40-name, high-turnover test of the order path.
- **Cost:** S (one spec amendment, one or two engine tasks, one paper task, one owner registration). Counted in `momentum`'s N.

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
