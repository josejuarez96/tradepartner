# Claims pilot: five reports as a claims-and-evidence graph (DRAFT)

**Date:** 2026-10-03  ·  **Status:** DRAFT for owner review, not a research report. No new evidence: every grade below is either the grade the source report gave, or "UNGRADED" where the report asserts without grading.  ·  **Purpose:** hand-write the claims layer for a few reports to learn what the eventual tool needs, before choosing one.

**Reports covered:**
- **NE**: [2026-10-03-ml-llm-neglected-equities-research.md](2026-10-03-ml-llm-neglected-equities-research.md)
- **INT**: [2026-10-03-tradepartner-ml-research-integration.md](2026-10-03-tradepartner-ml-research-integration.md)
- **QI**: [2026-10-03-quantitative-investing-research-brief.md](2026-10-03-quantitative-investing-research-brief.md)
- **G2**: [2026-09-26-g2-insider-purchases.md](2026-09-26-g2-insider-purchases.md)
- **EP**: [2026-10-02-edgar-data-pitfalls.md](2026-10-02-edgar-data-pitfalls.md)

Also referenced: ADR [0006](../decisions/0006-universe-and-cadence.md) (universe), ADR [0008](../decisions/0008-llm-role.md) (LLM role), [H1](../hypotheses/h1-momentum-12-1.md).

**Edge types used:** `supports`, `contradicts`, `refines` (narrows or corrects a claim without rejecting it), `depends-on` (a claim, a decision or a dataset), `tested-by` (a hypothesis).

---

## 1. Tensions found

These are the main thing the pilot produced. None is resolved here; each needs an owner decision or a research brief.

| # | Tension | Claims | What decides it |
|---|---|---|---|
| **X1** | **The neglected-equities thesis targets names the universe excludes.** NE's program is about small, low-coverage firms. ADR 0006 fixes the universe at the top 1,000 by market cap with a $5M median dollar-volume floor, because "anomaly returns are concentrated in microcaps and are eaten by costs there". G2 and QI both find the effects NE wants live mostly in small, illiquid, equal-weighted portfolios and shrink or vanish once those are removed. | NE-2, NE-3, NE-5 vs ADR 0006, G2-A, QI-4, QI-7, G2-S10 | **Decided by owner, 2026-10-03:** test NE's ideas **inside** the ADR 0006 universe, with "neglected" meaning underfollowed among the top 1,000. No new ADR. A separate small-cap research universe is reconsidered only if this shows something. |
| **X2** | **INT understates the ADR 0008 conflict.** INT says Stage 5 "conflicts with the current interpretation of ADR 0008". ADR 0008 option 4 explicitly rejects an LLM "as an **extractor** that pulls facts or numbers out of filings or text into the store", says it is rejected by charter principle 3 and process rules 4–5, and that it "stays rejected". So Stage 5 needs a **charter amendment** plus an ADR, not a reinterpretation. | INT-6, INT-9 vs ADR 0008 opt. 4, charter principle 3 | Owner: charter change. Can wait until Stages 2–4 show whether deterministic text features carry anything. |
| **X3** | **Old insider evidence is superseded for the tradable case.** NE cites Lakonishok & Lee (sample ends mid-1990s) for "purchases are informative, especially in small firms". G2 finds the post-2010, filing-timed, liquid-stock version NOT SUPPORTED, and the monthly version MIXED on pre-2008 evidence only. | NE-3 vs G2-A, G2-B | Already decided by evidence: NE-3 holds historically, G2 governs what we can trade. A test needs G2-B's design, not NE's. |
| **X4** | **Momentum's grade differs between sources.** H1's rationale rests on the handoff grading 12-1 momentum SUPPORTED. QI grades cross-sectional momentum MIXED "under the requested modern net standard". | H1 rationale vs QI-3 | No action: H1's prior is already centred on zero excess return and it runs as an engine test. The graph should still show the disagreement. |
| **X5** | **Conditional PEAD needs analyst data we don't have.** NE's PEAD features include analyst count, dispersion and revisions. QI lists I/B/E/S, FactSet and LSEG as price **unknown**. | NE-1, NE-2 vs QI-10 | A no-analyst version (earnings surprise from XBRL, attention from price/volume) is testable on free data; the analyst version needs a vendor ADR. |

---

## 2. Claims

Grades are as given by the source. "UNGRADED" means the report states it without a grade or citation. "Principle" marks a design or governance position rather than an empirical claim.

### From NE (neglected equities)

**NE-1.** Unconditional PEAD has weakened materially, or disappears with better risk and microstructure treatment.
- Grade: UNGRADED (NE cites no source for this).
- supports ← QI-2 (publication decay, in general).
- Gap: no repo report covers PEAD. Needs a brief before any hypothesis.

**NE-2.** PEAD (underreaction to earnings) is stronger in smaller, low-coverage firms.
- Grade: UNGRADED ("older literature", no citation).
- contradicts → ADR 0006 universe (**X1**); refined by QI-4 (size is MIXED; microcap costs dominate).
- depends-on → analyst coverage data (**X5**).

**NE-3.** Open-market insider purchases are informative, more than sales, and mostly in smaller firms.
- Grade: UNGRADED in NE; source Lakonishok & Lee (NBER w6656), a pre-2000 sample.
- refined by → G2-A, G2-B (**X3**). Consistent with G2's finding that predictability is concentrated in small and equal-weighted portfolios (S1, S5).

**NE-4.** Abnormal purchase intensity and context (role, history, clusters) carry more information than a binary insider-buy flag.
- Grade: UNGRADED in NE.
- supports ← G2 S2 (CMP): opportunistic buys VW five-factor 0.72%/month vs routine 0.09%, **1986–2007, gross**; G2 S9 clusters, pre-2015, timing unverified.
- So the claim holds in the old literature and is **untested post-2010 with filing-safe timing** (same gap as G2-B).
- tested-by → HS-2.

**NE-5.** Information in filings is incorporated less efficiently for firms with limited coverage.
- Grade: UNGRADED, no citation.
- contradicts → ADR 0006 universe (**X1**), if "limited coverage" means small caps.

**NE-6.** Changes in a firm's filings from one period to the next predict returns ("Lazy Prices").
- Grade: UNGRADED in the repo. Cohen, Malloy & Nguyen (JF 2020) is cited but no report has graded it.
- Prior: QI-2 (anomalies lose about 58% of their return after publication).
- Gap: needs a research brief (post-publication evidence, costs, large-cap subsample) before HS-1 is registered.
- tested-by → HS-1.

**NE-7 (principle).** An LLM should act as a measurement instrument, not the final predictor.
- contradicts → ADR 0008 option 4 (**X2**).

**NE-8 (principle).** Every extracted feature keeps full provenance: text, source, section, model and prompt versions.
- supports ← ADR 0008 point 6 (memo logging); rule 6 (`known_at`).

**NE-9 (principle).** Build the falsification machine before the prediction machine.
- supports ← QI-1, QI-11; existing trial registry and holdout.

### From INT (integration note)

**INT-1.** TradePartner already has most of the hard infrastructure: PIT discipline, security master, calendar, EDGAR ingest, trial registry, holdout, cost model.
- Grade: repo fact, not re-verified in this pilot.

**INT-2 (principle).** The missing piece is a research layer between PIT facts and portfolio construction: events, features, labels, datasets, model runs.

**INT-3.** ML needs walk-forward validation inside the unspent research region, with the existing holdout kept as the final gate.
- supports ← QI Q4 §6 (walk-forward, purging, embargo, lockbox).

**INT-4.** Deterministic filing-change features (TF-IDF similarity, changed-sentence fractions) should be tested before any LLM features.
- depends-on → NE-6 (grade), and 10-Q **document bodies and sections**, which the EDGAR ingest does not appear to hold today (EP covers indexes, headers, cover-page facts and Form 25 only).
- tested-by → HS-1.

**INT-5.** Form 4 ingest: open-market purchases (code P, acquired), with transaction date stored separately from filing time.
- refined by → G2 point-in-time notes:
  - use the EDGAR **acceptance timestamp**, not the filing date: over 70% of purchase filings arrive after the close, and Form 4s count as filed up to 10 p.m. ET;
  - 10b5-1 and discretionary trades have deemed execution dates up to 3 business days later;
  - the CMP classifier needs **3 prior calendar years** of each insider's trades.
- refined by → EP P12: acceptance times only from submissions (UTC), never repaired.

**INT-6.** Stage 5 (LLM semantic extraction) conflicts with "the current interpretation" of ADR 0008.
- contradicted by → ADR 0008 option 4 wording (**X2**): it is an explicit rejection, not an interpretation.

**INT-7.** A frontier model reading a historical filing may encode later information. Historical extraction research must be kept apart from prospective validation.
- supports ← ADR 0008 context (Lopez-Lira et al., MemGuard-Alpha, NumLeak); QI-8.

**INT-8.** Learned models (Elastic Net, gradient boosting) come only after baseline feature families show value, and they output a ranking score, never an order.
- supports ← QI-7 (ML gains shrink once microcaps and frictions are removed); QI Q7 honest-test table (compare against equal-weight ranks, ridge and elastic net).

**INT-9 (principle).** Reword "code computes numbers" so learned components may produce versioned research measurements, while portfolio, sizing, risk and execution stay deterministic.
- contradicts → charter principle 3 and CLAUDE.md non-negotiable 5 as currently written (**X2**).

### From QI (quantitative investing brief)

**QI-1.** Detecting a true annual Sharpe of 0.3 from one portfolio with 80% power takes about 69 years (a worked calculation, not a historical claim).
- supports → NE-9; bears on how much paper trading can prove (QI-9).

**QI-2.** Published anomalies lose about 26% of their return out of sample and 58% after publication (E1, McLean & Pontiff, T1).
- Prior for → NE-1, NE-6.

**QI-3.** Cross-sectional momentum: strong historical evidence, MIXED under a modern net standard.
- Grade: MIXED.
- contradicts (grade only) → H1's cited handoff grade (**X4**).

**QI-4.** Pure size is unreliable; microcap spreads, impact and delistings can dominate.
- Grade: MIXED.
- supports → ADR 0006's microcap exclusion; contradicts → NE-2, NE-5 (**X1**).

**QI-5.** Lottery, attention and overreaction effects: behaviour plausibly persists, but "avoid overpriced stocks" is easier than shorting them.
- Grade: MIXED for a tradable premium.
- refines → NE's attention thesis. Long-only use (avoidance filter) is the accessible version.

**QI-6.** Slow, liquid, profitability-aware stock selection has an unusually substantial foundation.
- Grade: SUPPORTED narrowly.
- Note: not in NE or INT, but the strongest-graded stock-selection claim in these five reports, and testable on XBRL fundamentals inside the ADR 0006 universe. Candidate HS-4.

**QI-7.** ML prediction gains shrink under economic restrictions (removing microcaps and distressed or high-volatility stocks, adding frictions). More predictors can worsen out-of-sample results.
- Grade: T1 sources [83, 84]; QI gives no single grade.
- supports → INT-8; contradicts → NE's small-cap ML framing (**X1**).

**QI-8.** Generic LLM signals: net portfolio alpha unknown. Pretraining on later news creates look-ahead even with old documents.
- Grade: INSUFFICIENT for deep price networks, reinforcement learning and general LLM signals.
- supports → INT-7, ADR 0008.

**QI-9.** Paper trading verifies operations but has little power to establish a low-Sharpe edge.

**QI-10.** Analyst-estimate, news and attention data (I/B/E/S, FactSet, LSEG): price unknown. EDGAR is free.
- Data constraint for → NE-1, NE-2 (**X5**).

**QI-11 (principle).** Budget trials before seeing results: a dozen prespecified variants per hypothesis, every variant counted, failed trials kept.
- supports → the trial registry; NE-9.

### From G2 (insider purchases)

**G2-A.** Buying liquid US stocks at or after Form 4 acceptance and holding for days earns a positive excess return since 2010.
- Grade: **NOT SUPPORTED**. Most liquid quartile +0.03% (t 0.37) to the next close, gross (S1, 2018–2023).
- refines → NE-3.

**G2-B.** A monthly-rebalanced, filing-timed portfolio of opportunistic or clustered insider purchases in non-microcap stocks earns a positive net excess return since 2010.
- Grade: **MIXED** (only pre-2008, gross support).
- refines → NE-3, NE-4. tested-by → HS-2.

**G2-S10.** Post-2005, non-microcap anomaly returns are near zero: a median of 7 bp/month (Chen & Welch 2026, abstract).
- Grade: prior only.
- supports → QI-2, QI-4; contradicts → NE's small-cap framing (**X1**).

### From EP (EDGAR pitfalls)

**EP-P12.** Acceptance times must come only from submissions (true UTC). SGML and FSN times are Eastern with no zone, and libraries get this wrong.
- Grade: SUPPORTED (3 of 3 fixtures matched).
- refines → INT-5, and every future event's `known_at`.

**EP-P13.** Conflicting or duplicate fact values are withheld, never picked.
- Grade: library-bug class SUPPORTED.
- Bears on → INT's `research_features` design: one feature value per key, conflicts quarantined.

---

## 3. Hypothesis sketches (not registered, not proposed for registration)

What a test of each claim cluster would look like, so its data needs are concrete.

| ID | Tests | Sketch | Universe | Blocked on |
|---|---|---|---|---|
| **HS-1** | NE-6, INT-4 | 10-Q MD&A and Risk Factors similarity to the prior filing ranks stocks; low-similarity names underperform over 1–3 months | ADR 0006 | A research brief grading NE-6; 10-Q body and section ingest |
| **HS-2** | G2-B, NE-4 | Monthly, acceptance-timed, long-only portfolio of CMP-opportunistic or clustered purchases; prior centred near 0 | ADR 0006 | Form 4 transaction ingest with ≥ 3 years of history before the test start |
| **HS-3** | NE-1, NE-2 | Conditional PEAD: XBRL-based earnings surprise × announcement return/volume × size; no analyst data in v1 | ADR 0006 | A PEAD research brief; earnings facts from XBRL; X5 for any analyst version |
| **HS-4** | QI-6 | Slow, annual profitability tilt (gross profits / assets) | ADR 0006 | Income-statement XBRL concepts in the store (the ingest appears to hold cover-page facts only today) |

---

## 4. What this means for the backfill

Data items the sketches need, as a first cut at backfill requirements. "Have?" is from a quick read of `src/` and EP, not a full audit.

| Data | Needed by | Have? | History needed |
|---|---|---|---|
| Prices, corporate actions, delistings | all | Yes | Test start minus 12 months |
| Filing index with acceptance timestamps (UTC) | all event clocks | Yes | — |
| Form 4 transactions (code, A/D, role, shares, price, transaction date, acceptance time) | HS-2 | **No** | Test start minus **3 years** (2007 for a 2010 start) |
| 10-Q/10-K document bodies, split into sections (MD&A, Risk Factors) | HS-1 | **No** | Test start minus 1 year (needs the prior filing) |
| Income-statement and balance-sheet XBRL facts (as filed, never restated) | HS-3, HS-4 | **Partly?** (cover-page facts only, needs checking) | Test start minus 2 years |
| Analyst estimates and coverage | HS-3 (v2), NE-2 | **No**; price unknown | — |

---

## 5. What the pilot taught about the eventual tool

- **Most of the value was in the tensions, not the claims.** Five tensions came out of about 35 claims. A tool should be judged by how well it surfaces contradictions and gaps, not by how many claims it stores.
- **Claims need a "source grade" and a "repo grade".** NE and INT state things without grades. A claim stated by a report is different from a claim graded by one, and the tool must keep both.
- **Decisions (ADRs, charter) are nodes too.** Two of the five tensions are with ADRs, not with other research. The graph needs ADR clauses as nodes, at clause level (for example "ADR 0008 option 4"), not just whole documents.
- **Data requirements belong on hypotheses.** They turn the graph into a backfill plan. That edge (hypothesis → dataset → history length) is worth making a first-class field.
- **Principles are not empirical claims.** They need a separate type, so no one grades them or mistakes them for evidence.
- **Hand-writing about 35 claims took one pass over five reports.** At the current corpus size (20 reports), a hand-maintained file plus a validating script looks sufficient. An LLM-assisted extractor would save little and would itself fall under ADR 0008 point 3.

## 6. Questions for the owner

1. ~~**X1:** test the neglected-equities ideas inside the ADR 0006 universe, or open a separate-universe ADR?~~ Decided 2026-10-03: inside the ADR 0006 universe.
2. Which sketches to take further? Each needs a research brief (HS-1, HS-3) or a data ingest (HS-1, HS-2, HS-4) first.
3. Should this format (claims, edges, tensions, data rollup) be extended to the other 15 reports by hand before the tooling decision?
