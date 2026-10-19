# Research Report: From replicating published strategies to original discovery, reconciled with the Research Lab

**Brief:** #1854 (the owner's brief "Research Lab: strategy research and original discovery framework", 2026-10-19, trimmed by the orchestrator to one report)  ·  **Date:** 2026-10-19  ·  **Status:** COMPLETE (desk reconciliation; nothing run)  ·  **Agent/model:** team discovery, claude-opus-5-5  ·  **Repo at:** `d1f08608`

**Question.** How should TradePartner move from testing published effects to proposing and testing its own hypotheses, without uncontrolled data mining, new infrastructure or misleading backtests? The 2026-10-18 readiness report ended with "if B10 dies, only a new idea through rung 1-2 arithmetic comes next" ([readiness §D](2026-10-18-daily-data-readiness.md#d-decision-memo)). This report says where that idea comes from and how it gets in.

**Answer in one line.** Most of the Research Lab already exists: the [ADR 0023](../decisions/0023-break-first-fast-books-paper-exam.md) kill ladder, the [strategy-lab](../specs/strategy-lab.md) family, sweep and promotion rules, the [research registry](../specs/research-registry.md) and the [ADR 0014](../decisions/0014-strategies-as-registered-objects.md) family seam. Three things are genuinely missing:
1. **A record of looks before rung 4.** Rung 1-3 reads and dropped ideas are written only in issue notes and frozen reports, and nothing counts them.
2. **Stage 6 of the research loop has not been done for the 10-18 verdicts.** No `TP-` claim records B3's retirement or B9's kill, and the LS- claims never entered `claims.toml`.
3. **No admission path for an idea with no published long leg.** Rung 2 cannot decide such an idea from open data (IRRX is the precedent).

All three are closed by rules and rows in existing class-A files. None needs code. Every idea tested so far is published or a light extension of a published one, and none is original. The next idea should be chosen for kill-cost and information value, not for originality. The literature gives no out-of-sample premium for originality or for a stated mechanism.

**Evidence states** (as in the readiness report): **IMPL** implemented · **TEST** tested (fixture or store) · **RES** researched · **DEC** decided · **DEF** deferred · **UNRES** unresolved.

**Caveat on the brief.** The brief's text is not in the repo. Section C's "§8 items" are the capability areas the orchestrator's trim names, plus the standard list for a strategy-research workflow (UNVERIFIED against the brief's own numbering).

---

## A. Landscape: everything tested or ranked so far

Source: **P** published replication · **X** extension (a published mechanism with our own measurement, screen or timing) · **O** original (no published test). Grades are the claims register's (S, M, N, I = SUPPORTED, MIXED, NOT SUPPORTED, INSUFFICIENT).

| Family / item | Source | Evidence grade | Final verdict (date) | Link |
|---|---|---|---|---|
| H1 / B1 12-1 momentum, monthly | P | HO-1 S vs G1-1, QI-3 M | Trial 2 PASS in-sample; holdout spent (trial 4: +4.4 pp vs SPY, red flag; B11: the win is beta 1.92). Tracking window only (10-08) | [h1](../hypotheses/h1-momentum-12-1.md), [B11](2026-10-10-b11-beta-alpha-split.md) |
| T114 momentum top-fraction × cadence sweep (6 variants) | P (parameter search) | as H1 | All lose to SPY (−2.6 to −6.4 %/yr); promotion refused; sweep retired (10-10) | [sweep](../sweeps/momentum-topfrac-cadence.md) |
| Daily momentum shakedown | P (machine test) | n/a | −6.13 pp/yr; operations book, not a bet | [file](../hypotheses/momentum-daily-shakedown.md) |
| B2 trend filter | P | TT-1, HO-5 M | Parked: the objective is not ADR 0005's (10-03, #659) | [backlog](hypothesis-backlog.md) |
| B3 gross profitability | P | QI-6 S (narrow) | **Retired** by its own rule: trial 3 −3.28 pp/yr, `dsr_excess` 0.3069 (10-18) | [note](2026-10-18-b9-kill-b3-retirement.md) |
| B3b cash profitability | X | proxy, ungraded | Retired with B3, never run (10-18) | same |
| B4 momentum + profitability | P | G4-2 M | Dropped (10-18) | same |
| B5 filing-text change → fundamentals | P | ER-4, ER-5 I | Proposed; needs a text corpus; D5 pause | [lazy prices](2026-10-03-lazy-prices-post-publication.md), [review](2026-10-03-disclosure-change-evidence-review.md) |
| B6 Lazy Prices → returns | P | ER-1 I (10-K M, 10-Q I) | After B5; prior near zero post-publication | same |
| B7 insider purchases, monthly | P | G2-B M, G2-A N | Proposed; needs a Form 4 ingest | [G2](2026-09-26-g2-insider-purchases.md) |
| B8 semantic disclosure labels | **O** (the only original line) | ER-14, ER-10 I | Paused (D5: labeling track) | [taxonomy](2026-10-03-semantic-taxonomy-v0.md), [ML brief](2026-10-03-ml-validation-methodology-brief.md) |
| B9 EAR drift (8-K 2.02 acceptance) | X (our acceptance times) | SH-6 M | **Killed** at rungs 1-2: long leg +0.07 %/mo, t 0.94; break-even 1.75-3.5 bp/side (10-18) | [note](2026-10-18-b9-kill-b3-retirement.md) |
| B10 short-term momentum, turnover-screened | P | SH-9 I vs SH-1 N | **Live**: rungs 2-3 due now, rung 1 about 10-30, rung 4 after T217c and T198 | [b10](../hypotheses/b10-short-term-momentum.md) |
| IRRX industry-relative reversal | P | SH-4 M | Rung 2 INSUFFICIENT (no public long leg); parked behind T198 (10-18) | [retry](2026-10-18-irrx-rung2-retry.md) |
| Plain reversal, industry momentum, SUE-PEAD, revenue drift, EA premium, dividend month, 10-K drift | P | SH-1, 3, 5 N; SH-7, 10 M; SH-8, 11 I | Not recommended (10-09) | [short-horizon](2026-10-09-short-horizon-candidates.md) |
| Beyond long-only #1-#6 (neutral composites, 125/25, pairs, EAR L/S, beta-hedged B3) | P | I or N, each refuted 3/3 by skeptics | No net edge; B11 done, B12 never run; shorting needs an ADR (10-10) | [beyond](2026-10-10-beyond-long-only.md) |
| Parked: search volume, retail herding, fire-sale, index adds, stat arb, BAB, value, LLM signals, deep learning, futures trend | P | G5-A N, QI-17 to 19, HO-6 to 9, QI-22 | Parked with reasons | [backlog §Parked](hypothesis-backlog.md#parked-not-recommended-now) |

**What the table shows.**
- Of 30 or so candidates, 1 is original (B8, a measurement pilot, not a strategy). No original return hypothesis has ever entered the funnel.
- Every fast idea died on cost arithmetic, as SH-2 says they would.
- The one slow idea that ran, B3, died in a 40-month window in which a mega-cap-led SPY beat every long-only tilt.
- These are the two walls a new idea must clear: **edge per unit of turnover** (rung 1) and **power in 40 in-sample months** (rung 4).

---

## B. The operating model: replication, extension, original, mapped onto what exists

The brief's evidence stages are the ADR 0023 ladder plus the strategy-lab rules. This section maps them and adds only the gaps (marked **GAP**).

| Stage (brief) | Existing rule | State | Replication | Extension | Original |
|---|---|---|---|---|---|
| 0 Idea enters | Backlog "Add an item": cite graded claims, prior, data, engine, cost, kill, information value; no graded claim means "needs research" with a brief ([backlog §Changing](hypothesis-backlog.md#changing-this-file), [research-program §3](../ways-of-working/research-program.md#3-hypothesis-backlog)) | DEC | As written | As written; name the parent claim | **GAP 3:** no claim exists by definition. Admission needs a written mechanism and a non-return validity prediction (section B2) |
| 1 Cost break-even | ADR 0023 rung 1: needed edge = 2 × one-sided turnover × measured cost per side; B9's line: kill if break-even ≤ 6 bp/side | DEC; B9 applied it | As written | As written | As written, with the prior read from the closest published analogue's gross |
| 2 Long leg in open data, post-2010 | ADR 0023 rung 2 (Chen-Zimmermann or open data); B9's line: kill if < 0.10 %/mo or t < 1.0 | DEC; B9, IRRX applied it | As written; **GAP 1:** the series and its construction are declared before reading | As written | **GAP 3:** undecidable from open data (IRRX). Replaced by **rung 2-O** (B2) |
| 3 Capacity at $100 | ADR 0023 rung 3 | DEC; not yet applied to any item | As written | As written | As written |
| 4 Harsh store backtest | ADR 0023 rung 4 (cost ×2, one-session delay, open fills; kill if net excess < 0 or `dsr_excess` < 0.5); a counted `trials` row; a new family's first registration is a sweep ([strategy-lab req 5(b)](../specs/strategy-lab.md)) | DEC; keys IMPL (T217), engine T217c open | As written | As written | As written; new root family via `config.FAMILIES` (ADR 0014) |
| 5 Placebo | ADR 0023 rung 5 (shuffle, random entry, extra lag); every run a trial | `placebo.py` IMPL; wiring T217b/c open | As written | As written | As written |
| 5b "Good number is a bug" | ADR 0023: shuffle, cost-shock, delay-shock **and top-names-removed** must pass | **GAP (small):** no task builds top-names-removed (grep finds none in `src/`, plans or specs) | — | — | — |
| 6 Promotion and exam | Strategy-lab req 4 (argmax, floor 0.5 at the SR* high-water mark, caps); ADR 0016 forward exam; ADR 0023 `paper.min_exam_fills` | IMPL (`promotion.py:206`) | Momentum family: tracking only (holdout spent) | Child of a spent family: tracking only | Root family: forward exam possible |
| 7 Result → register | research-program §6: `TP-` claim, `bears_on` links, backlog status | DEC; **GAP 2:** not done for B3's retirement, B9's kill or IRRX's rung-2 verdict; LS-1..20 not in `claims.toml` | — | — | — |

### B1. Replication and extension: nothing new is needed

The ladder already says, in cost order, what kills a replication or an extension.
- **Extension** is where our data has an edge: exact acceptance times, first-vintage facts, delisted names.
- An extension inherits the parent's published long leg at rung 2, and the B9 note shows how: the worse of the two files is used, and choosing the better one after seeing both counts as a forking path.

### B2. Original ideas: the one rule that is missing

An original idea has no published long leg. Two existing rules would otherwise force a bad choice: register a family and run rung 4 (engine work before any kill), or never test the idea.

The smallest fix is a different kill test at the same point on the ladder:
- **Rung 2-O, mechanism validity on non-return data.** The mechanism must name a fundamental or behavioural consequence that can be measured without returns. Examples: "late filers report worse next-quarter revenue growth"; "this disclosure change precedes an inventory build". The test is that prediction, on stored facts, registered as an `economic` experiment ([research-registry](../specs/research-registry.md), `research/experiment.py:53`) with `touches_returns = false`. Such a run **does not raise any family's N** (req 9, `store/research.py:1160`), so it costs no deflation budget. The kill rule is written in the experiment file before the run.
- **The order follows.** Rung 1 comes first, against the closest published analogue's gross and the idea's own turnover. Then rung 2-O. Only a pass reaches rung 3 and then a new root family at rung 4.
- **What rung 2-O cannot do.** It cannot show the effect is priced. It only stops ideas whose stated mechanism is false before any return is looked at. This is the ML validation brief's stage order (economic validity before returns, [§8](2026-10-03-ml-validation-methodology-brief.md#8-economic-validation-stream-stage-5)) applied to strategies.
- **Where it is written.** One paragraph in the backlog's "Changing this file" section (class A) and a cross-reference on the ADR 0023 rung 2 line at the next alignment PR (class B, batched).

### B3. What mechanism-first admission buys, and what it does not

**Against.** [Chen, Lopez-Lira and Zimmermann](https://arxiv.org/abs/2212.10317) mined 29,000 accounting ratios for t > 2.0 and got out-of-sample predictability similar to peer-reviewed predictors: about half survives the original sample in both groups. Predictors with risk-based explanations did **worse** out of sample. A stated mechanism therefore does not raise the expected return of a survivor.

**For.** We keep mechanism-first for three other reasons:
- **Fewer looks.** A mechanism fixes one construction before the data is read, which keeps N small.
- **A cheap kill.** The mechanism gives rung 2-O something to test without returns.
- **A clear failure.** When a mechanism-backed idea fails, the result says what was wrong.

**Uncertain.** Whether mechanism-backed survivors decay less in our universe is not known. Grade: MIXED (one high-quality study against, the field's convention for).

---

## C. Capability check

Classes: **S** already supported · **C** config only · **T** open task exists · **E** small extension (size S, no ADR) · **A** needs a new ADR. Paths are under `src/tradepartner/` at `d1f08608`.

| # | Capability | Class | Evidence (file:line) | Note |
|---|---|---|---|---|
| 1 | Hypothesis and trial registry (append-only; failures, refusals, reruns counted) | S | `store/registry.py:712` register, `:861` open_trial, `:972` write_result; DDL `store/schema.py:1490-1540` | IMPL, TEST |
| 2 | Family N, V, SR*, DSR | S | `store/registry.py:2018` (`_COUNTED_FROM`), `:1980` family_sharpes; `backtest/metrics.py:319,337,375` | V floor 0.04 (`lab.min_sharpe_variance_annual`) |
| 3 | Sweeps, declared count, promotion floor, family caps | S | `backtest/sweep.py:580` (cap 100), `backtest/promotion.py:206`, `backtest/holdout.py:403` | Only `momentum` has `sweepable_keys` (`config.py:1476`) |
| 4 | Non-backtest research runs (economic, robustness, return) with budgets, confirmatory vs exploratory | S | `research/experiment.py:53` (KINDS), `store/research.py:370,777`; return runs add to N `store/research.py:1160`, `backtest/results.py:165` | Built for labeling; usable for rung 2-O as is. A `touches_returns` run must name a family already in `hypotheses.families` (`research/experiment.py:379`) |
| 5 | New signal = new family | E per family | Literal `config.py:1277`, `FAMILIES` `config.py:1464`, `FamilySpec` `config.py:1450`, `signal_for` `backtest/strategies.py:284`, `_FAMILY_IO` `backtest/strategies.py:258` | ADR 0014: one module, one registry entry, one binding line. Not a plugin system, and none is needed |
| 6 | Point-in-time reads at t | S | `backtest/provider.py:95` protocol; `store/asof.py` readers (`statement_facts_as_of:784`, `filing_events_as_of:850`) | **E:** the provider exposes only `gross_profit` and `total_assets` (`backtest/provider.py:42`) and no filing-event read. Each new fact a family needs is one name and one reader |
| 7 | Filing acceptance times for 10-K/10-Q | S (stored), E (to read) | `statement_facts` carries `form`, `filing_accession`, `known_at` = acceptance (`store/schema.py:1110`; forms `config.py:421`) | 8-K 2.02 times: `filing_events` table (`store/schema.py:1146`) built, not populated (T164e, IRRX-parked). TP-2 timestamp shifts until the #1382 restamp |
| 8 | Cadence daily, weekly, monthly; per-position hold; band | S / T | `config.py:1282`; `hold` needs `daily` (`backtest/engine.py:1159`); hold wiring T209b | — |
| 9 | Costs: per side, spread, impact, ADV, ladder | S (pure) / T (engine) | `backtest/costs.py:51`; engine wiring T210c open | Rung 1's half-spread source is owner decision O-1 |
| 10 | Rung-4 delay shock, rung-5 placebos | T | keys `config.py:1349-1351`, `backtest/placebo.py:18,29,40`; engine T217c, trial tag T217b | Guard refuses non-defaults until T217c (`config.py:1364`) |
| 11 | Top-names-removed test (ADR 0023) | **E, no task** | none found | Can be a sweep axis or a fixed-block key on survivors only; open only when an item reaches rung 4 (four fields below) |
| 12 | Factor exposure | S (SPY beta only) | `backtest/metrics.py:171` (`_beta_alpha`) | Multi-factor spanning is a registered research run (B3-8 precedent), not a module |
| 13 | Sub-period and regime splits | C | run separate in-sample windows; no tool | Enough at 40 months; a regime tool would be search on a tiny sample |
| 14 | Capacity at $100 (rung 3) | S as arithmetic | ADR 0023 D6 feasibility rule (T206) | A note, not code |
| 15 | Data and code vintage, staleness | S | `store/registry.py:460` (`code_tree_sha256`), `:526` (`data_vintage`) | D8's single release id waits on T198 |
| 16 | Forward exams, tracking windows, operations books | S | ADR 0016 point 4; ADR 0023 `paper.min_exam_fills` | Every momentum book is tracking only |
| 17 | Shorts, long-short, borrow | **A** | long-only engine; `POSITION_SIDES` is paper schema only (`store/schema.py:1722`) | Charter, ADR 0010 and ADR 0014 bar it. [Beyond-long-only §steps 2-8](2026-10-10-beyond-long-only.md) lists the work |
| 18 | Log of ideas, looks and AI-proposed variants before rung 4 | **missing** | nothing in the store or the docs counts them (section D) | Section D: a rule plus a table in an existing file |
| 19 | AI-generated signals at runtime | **A** (barred) | ADR 0008 points 1-3 | AI may *propose* ideas. An LLM value never enters config or the store (point 3) |

**What the check shows.** Nothing on the brief's list needs new infrastructure before an idea survives rung 3. The engine work in flight (T209b, T210c, T217b/c) is rung 4's, and it is already sequenced.

---

## D. Search-process accounting: the gap and the smallest fix

### D1. What is counted today

| Look | Recorded? | Counted in N? | Where |
|---|---|---|---|
| Store backtest, sweep variant, placebo, failed or refused run | yes | yes (`ok` in-sample) | `trials` |
| Store research run on returns, exploratory included | yes | yes, by configurations (req 9, #1408) | `research_runs` |
| Variant declared but not yet run | yes | in "SR* at declared count" | sweep report (req 3) |
| Scratch dry run seen before registration | disclosed in the file (B3, #1143) | no | hypothesis file prose |
| Rung 1-3 arithmetic on open data (Chen-Zimmermann files) | issue note or desk note | **no** | #1765, #1734, the 10-18 notes |
| Second open series read after the first (B9's value-weighted file) | prose, as a disclosed forking path | **no** | B9 note §1 |
| Ideas ranked and dropped in a report (10-09: 8 not recommended; 10-10: 7 scouts × 3 skeptics) | frozen report | **no** | the reports |
| AI-proposed ideas never written down | **no** | **no** | — |
| Verdicts back into claims (`TP-`) | **no** for B3, B9 (rule in research-program §6) | n/a | — |

### D2. Why it matters, and how much

- **The looks overlap the test.** Rung 2 reads post-2010 US long legs. Rung 4 then tests on our store over 2020-08..2023-12, the same market and partly the same months. An idea that passes rung 2 because it did well over 2010-2024 has already been selected on part of rung 4's sample. Rung 4's DSR then understates the search, because N counts store trials only.
- **The size is small but not zero.** About 25 published candidates have been screened (the 10-09 and 10-10 reports); 2 reached rung 1-2 arithmetic (B9, IRRX) and B10 is next. For a family, the uncounted pre-selection is the handful of open series that decided which idea went forward.
- **The bigger risk is the next phase.** AI workflows can propose 50 variants in an afternoon. Today nothing would record the 47 that were dropped after a glance at a Chen-Zimmermann file.
- **The literature says this matters.** [Chordia, Goyal and Saretto (RFS 2020)](https://ideas.repec.org/a/oup/rfinst/v33y2020i5p2134-2179..html) put the false-discovery share at about 45 % when the search is ignored, and the hurdles at t ≈ 3.8 (time series) and 3.4 (cross-section). Harvey, Liu and Zhu (RFS 2016) put the hurdle at t > 3. Our DSR handles the store side. The open-data side has no counter.

### D3. The smallest rule (reuses existing records, no code)

**Rule L1, the ladder ledger.** Add a section "Ladder ledger" to [hypothesis-backlog.md](hypothesis-backlog.md) (class A). An idea gets one row the first time **any** rung-1-to-3 number is computed for it, whether it is killed or not:

| Field | Content |
|---|---|
| id | backlog id, or `L-<n>` for an idea that never became an item |
| date | absolute |
| origin | paper / extension / original; proposer: owner / agent / AI workflow (with its batch id) |
| mechanism | one line |
| declared read | the one open series (file, weighting, size screen, window) and the kill line, **written before the read** |
| looks | how many series and constructions were actually read (1 if the rule was kept) |
| rungs | the furthest rung passed; the verdict |
| link | the issue note or desk note |

**Rule L2, batches.** Any workflow (agent, AI, the owner's reading) that proposes ideas adds **one batch row**: date, proposer, number proposed, number admitted to rung 1, a link to the list. The ideas not admitted need no row each. The batch row makes the denominator visible.

**Rule L3, disclosure at rung 4.** A sweep file for an idea with ledger rows states, in its prior-evidence disclosure, the ledger's looks for that mechanism and its batch's denominator. This uses the existing "prior-evidence disclosure" section of the sweep and hypothesis templates. Promotion still uses the DSR at the family's high-water mark, unchanged.

**Rule L4, rung 2 windows (recommended, the owner may decline).** A new item's rung-2 read uses open data **ending 2019-12**, before every family's `in_sample_start` (2020-08-31). Rung 4 is then a test on months the screen never saw.
- Cost: post-2020 decay is not seen until rung 4.
- Benefit: rung 4's DSR stops overstating, and rung 4 becomes a true out-of-sample test of the rung-2 screen.
- B9 and IRRX used 2010-2024. This applies only to new items.

**Rule L5, close the loop.** Record the 10-18 verdicts as `TP-` claims: B3 retired with trial 3's numbers; B9 killed at rungs 1-2 with the yardstick table; IRRX rung 2 INSUFFICIENT. Add LS-1..20 from the beyond-long-only report and IRRX as a backlog row. This is the existing research-program §6 rule, already owed. It is not new work; any docs fold may do it.

**What this does not do.**
- It does not add the ledger's looks to N mechanically. That would need a code path and a rule for converting a public-data look into one trial, and the literature gives no agreed conversion.
- If L1-L4 prove insufficient (more than one item a month reaching rung 1, or any batch over 20 ideas), the upgrade path already exists: register the rung-2 read as a `return` experiment on an imported dataset version, once the family exists. Its configurations then count in N (req 9).

**Four-field check for L1-L5.**
- **Existing-work search:** backlog, claims.toml, trial registry, research registry, the strategy-lab declared count, the 10-18 notes.
- **Why insufficient:** none records pre-rung-4 looks or dropped ideas (D1).
- **Minimum scope:** one section and one paragraph in `hypothesis-backlog.md`, about 4 `TP-` claims and 20 LS- claims in `claims.toml`. All class A; no code.
- **Decision unlocked:** AI-generated ideas can enter the ladder without uncounted search, and the next ADR 0023 revisit can read the denominator.

---

## E. Decision memo (standalone)

**For the owner, 2026-10-19. One decision at a time; recommendations first.**

**Where we are.**
- About 30 candidates have been tested or ranked since 2026-09-24. All are published or light extensions. Only B8 (a labeling pilot, paused) is original.
- The machinery for original discovery mostly exists:
  - the kill ladder;
  - family, sweep and promotion accounting;
  - counted research runs;
  - one family seam (ADR 0014).
- It needs no new infrastructure.

**What is missing** (section D):
- a record of looks before rung 4 and of dropped and AI-proposed ideas;
- `TP-` claims for the 10-18 verdicts;
- a rung-2 test for ideas with no published long leg ("rung 2-O": check the mechanism on stored facts first, with no look at returns and no cost to N).

**Published or original: the balance.**
- **Stay mostly with published and extension ideas for now.** The best evidence (Chen, Lopez-Lira, Zimmermann) finds mined and published predictors decay alike, about 50 %, and mechanism stories do not predict which survive.
- **Originality earns no premium. Cheap kills do.** Every published long-only route so far died on one of two walls: cost per unit of turnover (fast ideas) and a 40-month sample (slow ideas). An original idea faces the same walls.

**Should original exploration start now?** Only as admission rules (L1-L5 and rung 2-O): no engine work and no new family until an idea passes rungs 1, 2(-O) and 3. It can run in parallel with B10's wait, because rungs 1-3 are read-only.

**Pilot with the most learning value.**
- Pilot A (liquidity-conditioned price signals) **is B10**. Finish it.
- After B10, the next ladder item is **Q1 below**: repurchasers' long leg. It is cheap to kill, and its verdict answers a bigger question: does any slow fundamentals tilt deserve rung 4 after B3?
- Pilot B (disclosure divergence) **stays paused unless you revive it**. If you do, the cheapest first test reopens nothing: ML brief E1 "spec A" on stored statement facts (next-quarter revenue-growth change from as-filed facts, no text). It is an `economic` run with no cost to N, and it sets the bar any text feature must later beat. **This is your decision.**

**Minimum justified work** (all class A docs, no code):
1. L5: the `TP-`, LS- and IRRX backlog rows (owed).
2. L1-L3: the ledger section and the batch and disclosure rules.
3. Rung 2-O: one paragraph in the backlog.
4. Q1's rung 1-2 note, after B10's rungs 2-3 notes.

**Deferred.**
- B5-B8, the text corpus and Form 4.
- Shorting (needs an ADR) and B12.
- The top-names-removed test (until an item reaches rung 4).
- Automating ledger looks into N.
- Any regime or factor tooling.

**Evidence that would justify more investment.**
- Any item passing rung 4 at harsh settings, with placebos clean: then build its forward book.
- Rung 2-O passing for an original mechanism: then a family entry.
- If B10 and Q1 both die at rungs 1-2, ADR 0023's revisit applies. The evidence would then point at the **long-only, top-1,000 constraint** rather than at the supply of ideas: anomaly profits sit mostly in short legs (Stambaugh, Yu, Yuan 2012; LS-20). That is the moment for the shorting decision, not before.

**Near-term sequence.**
1. Now: L5 claims fold; B10 rungs 2-3 notes.
2. Then: the L1-L3 ledger and the rung 2-O paragraph; Q1's rung 1-2 note, declared before the read.
3. About 10-30: B10 rung 1.
4. After T217c and T198: B10 rung 4/5. Q1 enters rung 4 only if it passed rungs 1-3.

**Stop.**
- Ranking ideas by novelty or by story.
- Reading a second open-data series after the first and choosing between them.
- AI idea lists without a batch row.
- Plugin or strategy-factory designs.
- Any engine work for an idea that has not passed rung 3.

---

## Q1. The first falsifiable question after B10 (sketched, not run)

**Why this one.** It is the cheapest decidable item that stresses the slow-signal wall rather than the cost wall. It uses data the universe already reads (the shares fact, with `known_at` = acceptance), and its rung 2 is decidable from open data. It is an **extension** (P: Pontiff-Woodgate 2008, Daniel-Titman 2006; X: our acceptance-timed, split-adjusted shares fact in the top-1,000). The claims behind it are QI-15 MIXED and LS-20 I. Under the backlog rule it enters as **"needs research"**, with a one-claim brief.

**Question.** In the ADR 0006 top-1,000 universe, does an equal-weight long book of the lowest net-share-issuance quintile (largest net repurchasers over the last 12 months) beat SPY net of costs?
- **Issuance:** log change in split-adjusted shares outstanding between the latest fact known at t and the latest fact known at t − 12 months.
- **Book:** monthly rebalance, about 60 names.

**Mechanism and its non-return prediction.** Managers repurchase when they think the shares are undervalued, and the market underreacts. Prediction: repurchasers' next-four-quarter operating cash flow over assets is no worse than issuers'. That is a rung 2-O check, available if rung 2 cannot decide.

**Rung 1 (arithmetic).**
- Turnover: the signal moves when a new 10-Q/10-K shares fact arrives (quarterly), so one-sided turnover is about 8-12 %/month (an estimate; B3's similar construction is the analogue).
- Break-even per side = long-leg gross per month / (2 × turnover). At a long leg of 0.10 %/mo: 10 / (2 × 0.10) = **50 bp** per side, against about 6 bp measured plus a 5-10 bp half-spread (HO-14).
- **Kill if break-even ≤ 3 × measured cost per side.** Expected to pass; this rung is not where Q1 lives or dies.

**Rung 2 (open data), declared before reading.**
- One series: Chen-Zimmermann `ShareIss1Y`, size-screened file (ex NYSE p20, B9's precedent), long leg (low-issuance portfolio).
- Window 2010-01..2019-12 (rule L4).
- **Kill if the long leg is < 0.10 %/mo or t < 1.0** (the B9/IRRX line).
- The value-weighted file is not read. The 5-year `ShareIss5Y` is not read. Reading either is a new ledger row.
- **Prior:** near zero. McLean-Pontiff's decay applies, and LS-20 and Stambaugh-Yu-Yuan put the issuance premium mostly in the short leg. Expected verdict: kill (about 60-70 %, an informed guess, not a measurement).

**Rung 3.** About 60 names × the D6 feasibility rule: fractional notional orders, about 6-10 trades a month. Expected to pass.

**Rung 4, only on a pass.**
- A new root family `issuance` (ADR 0014): one `FamilySpec`, one signal, one reader calling the shares read at t and at t − 12 months, about size S plus a spec amendment.
- Registered as a one-value sweep, harsh settings, in-sample 2020-08-31..2023-12-29.
- **Data risk:** split mis-adjustment or CIK breaks in the shares fact look like huge issuance. This is the same failure mode B10's file warns about, and the D1 residue (BLK, XOM chains) sits exactly there.
- **Power risk:** 40 months, as B3. Rung 4 can kill, not confirm.

**Decision unlocked.**
- **Pass at rung 2:** the slow long-only line is not dead; Q1 goes to rung 3 and then to a family.
- **Kill:** together with B3, it retires slow fundamental long-only tilts in this universe. If B10 also dies, the ADR 0023 revisit gets a concrete reading: the constraint, not the idea supply, binds.

**Four fields.**
- **Existing-work search:** backlog (no issuance item), claims (QI-15, LS-20), beyond-long-only §213 (the "lead for later").
- **Why insufficient:** no long-only rung-2 read of issuance exists.
- **Minimum scope:** one backlog row "needs research", one desk note with the rung 1-2 table; no code.
- **Decision unlocked:** as above.

---

## Idea origination: three methods, ranked

Ranked by fit to our data (daily bars, exact SEC acceptance times, first-vintage statement facts, no quotes) and by cost per kill. All three feed the same ledger (L1-L2) and the same ladder.

| Rank | Method | What it produces | Admission | Fit and risk |
|---|---|---|---|---|
| 1 | **Pre-declared catalog screen with a time split.** Read the Chen-Zimmermann catalog's size-screened long legs **only to 2019-12**, filtered by one pre-declared rule: long leg ≥ 0.10 %/mo, t ≥ 2, and break-even ≥ 3 × measured cost at the signal's turnover. | A short list in one sitting, every look counted (one batch row, N = catalog size) | Each survivor still needs a mechanism line and rung 3; rung 4 on 2020-08+ data is out of sample to the screen | Best fit: honest mining that the literature finds performs like peer review (CLZ). Risk: the catalog is US-wide, not our top-1,000 |
| 2 | **Data-edge extensions.** A published mechanism re-measured where our data is better: acceptance-time ordering of facts, first-vintage values, delisted names, the 8-K 2.02 instant | Extensions like B9 and Q1 | Rung 2 from the parent's open series; L4 window | Our only real edge is timing and point-in-time integrity. Risk: B9 shows timing precision does not rescue a thin long leg |
| 3 | **Post-mortem and AI proposals.** Derive mechanisms from our own failures (B11: H1's holdout win was beta 1.92; B3's mega-cap shortfall), or from an AI workflow | Original ideas | A mechanism card with a non-return prediction, then rung 1 and rung 2-O | Most original, weakest prior. Every AI list is a batch row; the AI proposes, the owner admits; ADR 0008 point 3 keeps LLM values out of config |

**How AI-proposed ideas count.** An idea counts the same whoever proposed it. The proposal list is one batch row (L2), each admitted idea gets a ledger row (L1), and every rung-4 run counts in N as any trial does. An AI workflow may not read open return series beyond the one declared read per idea. If it does, every extra read is a look (D1's forking path), recorded as such.

---

## Disconfirmation

- **Searched for:** evidence that mechanism-backed, original or AI-generated strategies survive better out of sample; evidence that published anomalies replicate well (against this report's low priors); evidence that pre-registration of looks is unnecessary.
- **Found against our caution:**
  - Jensen, Kelly and Pedersen ("Is there a replication crisis in finance?", JF 2023) find most factors replicate under a Bayesian, cluster-based view, and that the multiple-testing haircuts above may be too harsh. This argues the priors here may be too low for published long-short factors. It says little about long-only legs in large caps net of retail costs.
  - Fama and French ("Dissecting anomalies", JF 2008) find net issuance among the anomalies that hold in big stocks too (supports Q1's rung 2 passing; pre-2010 data).
- **Found against originality as a goal:** Chen, Lopez-Lira, Zimmermann (above), and McLean and Pontiff (JF 2016: returns 26 % lower out of sample, 58 % lower after publication; QI-2, UNGRADED in our register).
- **Found against the long-only legs:** Stambaugh, Yu and Yuan (JFE 2012): anomaly profits concentrate in the short leg after high sentiment. Novy-Marx and Velikov (RFS 2016): most high-turnover anomalies do not survive costs. Hou, Xue and Zhang (RFS 2020): 65 % of 452 anomalies fail |t| ≥ 1.96 once microcaps are controlled (cited in [G4](2026-09-25-g4-signal-combination.md)).
- **Uncertainty kept:**
  - How much an open-data look should count against a store test is not settled in the literature; L1-L4 record and separate the looks rather than convert them.
  - Q1's verdict odds are a guess.
  - The turnover estimate for Q1 is an analogy to B3, not a measurement.

## UNVERIFIED items

- The exact file name and size-screen variant of Chen-Zimmermann `ShareIss1Y` (checked at Q1's rung 2, before reading the numbers).
- The brief's own §8 numbering (section C caveat).
- Q1's 8-12 %/month turnover.
- Johnson and So (JFQA 2018, scheduling of earnings dates) was considered for method 2. Its published return magnitude was not confirmed, so it is not used.

## Sources

- **Decisions:** [ADR 0005](../decisions/0005-objective-benchmark-stop-criteria.md), [0008](../decisions/0008-llm-role.md), [0014](../decisions/0014-strategies-as-registered-objects.md), [0016](../decisions/0016-development-boundary-and-forward-exams.md), [0017](../decisions/0017-fast-paper-and-machine-readiness-gate.md), [0023](../decisions/0023-break-first-fast-books-paper-exam.md).
- **Specs and process:** [strategy-lab](../specs/strategy-lab.md) (Definitions, reqs 3-5), [backtest](../specs/backtest.md), [research-registry](../specs/research-registry.md) (reqs 7-9, #1408), [research-program](../ways-of-working/research-program.md), [backtest plan](../plans/backtest.md) (T165e, T209b, T210c, T217-T217c).
- **Research:** [hypothesis-backlog](hypothesis-backlog.md), [claims.toml](claims.toml) (HO-1, G1-1, QI-2, QI-6, QI-15, SH-1..12, ER-1..14, G2-A/B, TP-1..4), [trial-registry](trial-registry.md), [short-horizon](2026-10-09-short-horizon-candidates.md), [beyond long-only](2026-10-10-beyond-long-only.md) (LS-1..20, §213), [B11](2026-10-10-b11-beta-alpha-split.md), [B9/B3 note](2026-10-18-b9-kill-b3-retirement.md), [IRRX long leg](2026-10-18-irrx-long-leg.md), [IRRX retry](2026-10-18-irrx-rung2-retry.md), [readiness](2026-10-18-daily-data-readiness.md), the 10-03 set ([semantic taxonomy](2026-10-03-semantic-taxonomy-v0.md), [typed classifiers](2026-10-03-typed-classifiers-financial-disclosures-research.md), [lazy prices](2026-10-03-lazy-prices-post-publication.md), [disclosure review](2026-10-03-disclosure-change-evidence-review.md), [semantic feature spec](2026-10-03-prospective-semantic-feature-spec.md), [ML validation](2026-10-03-ml-validation-methodology-brief.md), [claims pilot](2026-10-03-claims-pilot.md)).
- **Issues:** #836 (trial results 1-17 and owner reads 10-06..10-18), #1143, #1292, #1301, #1352, #1353, #1358, #1452, #1734, #1765, #1792, #1820, #1824.
- **Code** (`d1f08608`): as cited in section C.
- **External:**
  - Chen, Lopez-Lira, Zimmermann, "Does Peer-Reviewed Research Help Predict Stock Returns?" ([arXiv 2212.10317](https://arxiv.org/abs/2212.10317)).
  - Chordia, Goyal, Saretto, "Anomalies and False Rejections", RFS 33(5) 2020 ([RePEc](https://ideas.repec.org/a/oup/rfinst/v33y2020i5p2134-2179..html)).
  - Harvey, Liu, Zhu, RFS 2016.
  - Hou, Xue, Zhang, RFS 2020.
  - Jensen, Kelly, Pedersen, JF 2023.
  - McLean, Pontiff, JF 2016.
  - Novy-Marx, Velikov, RFS 2016.
  - Stambaugh, Yu, Yuan, JFE 2012.
  - Fama, French, JF 2008.
  - Pontiff, Woodgate, JF 2008.
  - Daniel, Titman, JF 2006.
  - Bailey, López de Prado, J. Portfolio Management 2014 (DSR).
  - Johnson, So, JFQA 2018 ([RePEc](https://ideas.repec.org/a/cup/jfinqa/v53y2018i06p2431-2464_00.html), not relied on).
