# ML Validation Methodology Brief: semantic measurement, economic content, and event streams

**Brief:** #618  ·  **Date:** 2026-10-03  ·  **Status:** DRAFT methodology for owner review. It is a design, not a result: nothing here was run.  ·  **Agent/model:** main session (research lead); citations checked by a research agent on 2026-10-03 (see Sources for what was and was not verified)

**Companion artifacts (#618):** [taxonomy v0](2026-10-03-semantic-taxonomy-v0.md) · [annotation protocol](2026-10-03-disclosure-annotation-protocol.md) · [evidence review](2026-10-03-disclosure-change-evidence-review.md) · [prospective feature spec](2026-10-03-prospective-semantic-feature-spec.md).

**Inputs:** 2026-10-03 notes on branch `spike/ml-llm-research-notes` (`587cf8a`, unmerged): typed-classifiers results (**TC**), integration note (**INT**), quantitative-investing brief (**QI**), claims pilot (**CP**). On main: [handoff](2026-09-24-initial-research-handoff.md), [G2 insider purchases](2026-09-26-g2-insider-purchases.md), [EDGAR pitfalls](2026-10-02-edgar-data-pitfalls.md) (**EP**), [backtest spec](../specs/backtest.md), [event-data spec](../specs/event-data.md) (draft), ADRs [0005](../decisions/0005-objective-benchmark-stop-criteria.md), [0006](../decisions/0006-universe-and-cadence.md), [0008](../decisions/0008-llm-role.md).

---

## 1. Objective

Fix, before any experiment, how TradePartner will decide whether a semantic feature measures what it claims, carries economic information, and adds anything to a ranking. Fix the same for the two non-semantic event streams (insider purchases and conditional PEAD). The aim is to make self-deception expensive: every split, metric, threshold, comparison set and multiplicity rule is written down here, or in a registered pre-registration, before data are seen.

## 2. Scope and stage gates

The program is a sequence of gates. A stage starts only when the previous gate has passed and the owner has recorded that.

| Stage | Question | Gate to pass | Artifact |
|---|---|---|---|
| 1. Define | Is each concept precise? | Owner review of taxonomy v0 | [taxonomy v0](2026-10-03-semantic-taxonomy-v0.md) |
| 2. Label | Can humans label it reliably? | Protocol §8 gates G1–G5 per dimension × mode | [protocol](2026-10-03-disclosure-annotation-protocol.md) |
| 3. Measure | Can a system reproduce the human labels, beyond simple baselines? | §5 confirmatory tests and acceptance thresholds | this brief §5 |
| 4. Validate semantics | Are its probabilities usable, and does confidence find the hard cases? | §5.5–5.7 | this brief |
| 5. Validate economics | Does the measured concept predict the economic variable it names, beyond controls? | §8 | this brief |
| 6. Validate prediction | Does it add return information beyond market, fundamentals and deterministic text? | §9 | this brief |
| 7. Validate portfolio use | Does it survive costs, turnover, the holdout and prospective data? | §9.4, plus a charter amendment and an ADR | not yet written |

Stage 6 cannot start for a feature that failed stage 5. Classifier accuracy alone never promotes a feature to stage 6.

### Governance gates

These are what the existing rules already require. This brief does not change them.

| Activity | Allowed under today's rules? | What unlocks it |
|---|---|---|
| Taxonomy, protocol, human annotation, deterministic text features, classical classifiers trained on our own labels (arms 1–4) | Yes: code computes them, and labels are human decisions (ADR 0008 point 3). Code still needs a spec and plan tasks (CLAUDE.md rule 2). | Spec + plan |
| Any pretrained language model in an experiment (arms 5–9: encoder, Jev, small or frontier generative), even offline | **No.** ADR 0008 point 1 forbids an LLM SDK or client before Phase 5, and a PR that adds one is out of scope. A local pretrained encoder is arguably not an "LLM"; **owner decision** on whether ADR 0008 covers it. | A new ADR defining a **research-measurement boundary**: a segregated research environment, model outputs that can never reach the store, decision tables or config, and the ADR 0008 point 2 style import/table tests. Plus a research spend ceiling above $0. |
| Prospective capture (a scheduled job calling a model on new filings) | **No**, same reason, plus it is the "extractor" ADR 0008 option 4 rejects if its output enters the store. | The ADR above, with capture written to a separate research store. |
| Any model-derived feature in a ranking, portfolio or paper run | **No.** It conflicts with charter principle 3 and ADR 0008 option 4 (CP tension X2). | A **charter amendment** plus an ADR, citing stage 5–6 evidence and prospective results |

## 3. Splits, leakage and dependence

### 3.1 Semantic benchmark (stages 3–4)

- **Issuer-level assignment first.** With a seed published before assignment, every eligible issuer is assigned to one group: `dev` (60%), `cal` (15%) or `test` (25%). Pilot issuers are forced into `dev` (protocol §4.4).
- **Time second.** The sealed **test period** is the most recent span with enough pairs: proposed as current filings accepted 2024-01-01 → 2025-12-31, frozen at pre-registration. `dev` and `cal` pairs come from earlier filings.
- **Primary sealed test:** `test`-group issuers × test period, so issuers are unseen and the period is out of time. **Secondary:** `dev`-group issuers × test period, so issuers are seen but the period is out of time. Comparing the two estimates the issuer-style effect. Both are representative samples with known inclusion probabilities. A challenge sample, built as in protocol §4.2, is reported separately.
- **No document crosses splits:** a filing that appears in any pair belongs to exactly one split, and pairs that would straddle are dropped.
- **Near-duplicate boilerplate.** Character-shingle MinHash over evidence passages. Any `dev`/`cal` passage with estimated Jaccard ≥ 0.8 to a sealed-test passage is removed from training and calibration (the side that is cheap to trim). The count is reported. The sealed set is never trimmed after it is drawn.
- **Sealed means sealed.** Prompts, instructions, retrieval settings, features, hyperparameters, calibrators and routing thresholds are all fixed using `dev` and `cal` only. The sealed test is scored once per pre-registered configuration. Every scoring is a registry entry (§10). A second look is a new, flagged entry, as with the backtest holdout.
- **Size:** set by a power calculation **after** the pilot gives class prevalence, not fixed here. For orientation (TC §15): proving an accepted adverse-label precision with a one-sided 95% lower bound ≥ 0.90 needs on the order of **100+ accepted adverse predictions per dimension** when true precision is near 0.95. For example, 95/100 gives a lower bound of about 0.90, and 60/60 only about 0.951. TC's 900/500/600 allocation is a planning number.

### 3.2 Economic and return models (stages 5–6)

- **Walk-forward, expanding window.** Each fold trains on all data before its validation year, validates on that year, and tests on the next. Hyperparameters are chosen inside the training window (nested). The test year is never used for choices.
- **Purging and embargo** (López de Prado 2018, ch. 7). An observation whose **label window** overlaps the test window is purged from training. After each test window, an embargo of `h` sessions (the label horizon) is dropped. Labels are as-filed and timestamped, so a label's window runs from the feature's `known_at` to the end of its horizon.
- **Overlapping labels.** Event horizons longer than the event spacing (60-session returns on quarterly events; monthly ICs on 3-month returns) are handled in two ways: inference is dependence-aware (§3.3), and effective sample sizes are reported (§3.4).
- **Final gate.** The existing per-hypothesis holdout ([backtest spec](../specs/backtest.md) reqs 8–12) stays the last gate. For any feature from a **modern pretrained model**, the holdout should be **prospective only**, made of filings after the feature configuration was frozen (§7).
- **Fit-in-fold.** Vocabularies, IDF weights, dictionary normalizations, embeddings that need fitting, scalers and feature selection are fitted inside each training fold only (TC §19).

### 3.3 Dependence

| Source | Treatment |
|---|---|
| Multiple observations per pair, pairs per issuer | Issuer-cluster bootstrap for all benchmark statistics and model differences (paired: resample issuers, score all systems on the same resample) |
| Firm and time effects in panels | Two-way clustered standard errors, by firm and calendar quarter (Petersen 2009; Cameron, Gelbach & Miller 2011) |
| Cross-sectional correlation of returns | Monthly cross-sectional statistics (rank IC, quantile spreads), then time-series inference on the monthly series, with Newey–West lags ≥ horizon − 1 |
| Few clusters in a sector slice | Cluster bootstrap refinements (Cameron, Gelbach & Miller 2008). A slice with fewer than ~30 issuers is "underpowered", never "equivalent". |

### 3.4 Effective sample size (always reported)

- Benchmark: number of issuers and pairs, never only observations.
- Event studies: number of distinct event-months, plus the overlap-adjusted count.
- Return tests: number of independent periods, about T/h for horizon h. The universe has data from 2016 (ADR 0009), so the in-sample span is short.

**Illustration only:** with about 96 monthly ICs and an IC standard deviation of 0.08, a mean IC must exceed about 0.016 to reach t ≈ 2. QI-1 makes the same point for Sharpe ratios: decades are needed for small edges.

## 4. Statistical discipline that applies to every stage

1. **Pre-registration before the data that decide it.** It must list: hypothesis; dataset and sampling frame; taxonomy, feature-set and model versions; primary metric; secondary metrics; the comparison set; CI method; noninferiority margin, if any; multiplicity method; acceptance thresholds; exclusion and missingness rules; and what counts as a new experiment (TC §23). For return hypotheses this is the existing hypothesis file plus `hypothesis register`. For non-backtest experiments it is a registry entry (§10).
2. **Confirmatory vs exploratory.** Only prespecified tests in a declared family are confirmatory. Slices, extra horizons and post-hoc features are labeled exploratory in every table.
3. **Multiplicity.**

| Family | Method |
|---|---|
| Confirmatory model comparisons | Holm (1979), which controls FWER under any dependence |
| Hypotheses with a natural order (insider stream) | Fixed-sequence gatekeeping at α; stop at the first non-rejection |
| Exploratory screens | Benjamini–Hochberg (1995) FDR, labeled exploratory |
| Comparisons over a large model or spec set | White (2000) Reality Check, Hansen (2005) SPA, or Romano–Wolf (2005) stepwise |
| Strategy-level Sharpe | The existing deflated Sharpe (Bailey & López de Prado 2014), with the registry's trial count. The hurdle context is Harvey, Liu & Zhu (2016). |

4. **Every run is counted**, failures included (domain rule 2). Hyperparameter searches count as trials of the family.
5. **Priors are stated.** Published anomalies lose about 26% out of sample and 58% after publication (McLean & Pontiff 2016). ML gains shrink sharply once microcaps, distressed firms and costs are removed (Avramov, Cheng & Metzker 2023). Every return pre-registration states an expected effect at or below these haircuts.

## 5. Semantic benchmark statistical plan (stages 3–4)

### 5.1 Arms (all mandatory; none may be dropped for convenience)

| # | Arm | Regime | Probability output |
|---|---|---|---|
| 1 | Majority class (per dimension, from `dev`) | none | constant vector |
| 2 | Financial dictionary features (Loughran–McDonald counts in each span and their change) + deterministic change features (§6) + multinomial logistic regression | supervised | yes |
| 3 | Paired TF-IDF (previous, current, difference) + logistic regression | supervised | yes |
| 4 | Paired TF-IDF + linear SVM (with Platt or isotonic calibration on `cal`) | supervised | after calibration |
| 5 | Financial encoder / embedding pair classifier (domain-adapted encoder, fine-tuned or frozen embeddings + head) | supervised | yes |
| 6 | Jev, pinned version (zero-shot, fixed instructions) | zero-shot | yes (vendor probabilities) |
| 7 | Small generative model, constrained output (label enum) | zero-shot | label-token probabilities if exposed, else none |
| 8 | Frontier generative model, constrained output | zero-shot | same |
| 9 | Confidence-gated cascade (best cheap arm → fallback → `unresolved`) | composite | routing score |

**Fairness rules** (TC §14):
- Zero-shot and supervised regimes are reported separately and together.
- Every arm gets a **prespecified development budget**: a fixed number of prompt or instruction variants for 6–8, and a fixed hyperparameter grid for 2–5, all evaluated on `dev`. Learning curves are reported for the supervised arms at 25/50/100% of `dev`.
- No arm is asked for explanations in the main comparison. Evidence selection is a second endpoint, measured on all arms through span attribution.
- A cosine-similarity score is not a classifier. Deterministic features enter only through the disclosed supervised mapping of arm 2, and are kept raw for the economic ablations.
- Arms 5–9 need the governance gate in §2.

### 5.2 Experiments

- **A, supplied evidence.** Every arm receives the same human-verified matched spans plus metadata (dates, form, scope, basis). This isolates classification.
- **B, end-to-end.** Every arm starts from the same archived filings (hashes recorded) and must locate evidence, align period and scope, classify, return provenance and abstain when appropriate. B reports **separately**:
  - evidence-retrieval recall against gold spans;
  - alignment accuracy (period, scope, basis);
  - classification accuracy on correctly retrieved items;
  - provenance validity: returned offsets resolve, the text hash matches, and a human checks on a sample that the span supports the label;
  - abstention appropriateness.

  Failures are attributed to one of: parse, retrieval, alignment, classification, provenance.

### 5.3 Endpoints

- **Primary:** the mean, over the frozen core dimensions, of per-dimension **macro-F1 on the joint label J** (protocol §8), in the primary measurement mode frozen in taxonomy v1, on the **primary representative sealed test**.
  - Classes with no test support are dropped and listed.
  - The primary score is **forced-choice**: abstention is scored in §5.6.
  - For arm 9, `unresolved` is scored as a never-correct prediction, so the cascade cannot gain by abstaining.
- **Secondary:**
  - each dimension separately;
  - evidence-status macro-F1;
  - adverse-state precision and recall (native adverse label, taxonomy §4.5);
  - confusion matrices;
  - directional coverage (sufficient-status cases given a direction and accepted);
  - accepted-set error;
  - unresolved rate;
  - provenance validity;
  - every metric by document type, period block, cap tercile and sector (exploratory).
- **Human reference.** Each arm's macro-F1 against adjudicated gold is reported **beside** each annotator's macro-F1 against the same gold. An arm "at human level" means within the annotators' range, not "above 0.85".

### 5.4 Confirmatory comparisons, CIs and acceptance

Prespecified family, Holm-adjusted:
- **H-M1 to H-M5:** each of arms 5, 6, 7, 8 and 9 beats the **best of arms 2–4** (chosen on `cal`, frozen) on the primary endpoint. Superiority means Δ > 0.
- **H-M6 (only if cost motivates adoption):** the cheapest arm passing H-M is **noninferior** to the best arm, with margin **0.02 macro-F1** (TC §15).

CI method: paired issuer-cluster bootstrap, 10,000 resamples, percentile intervals. One-sided p-values from the bootstrap distribution of Δ, then Holm.

**Acceptance thresholds** (proposals; frozen at pre-registration, after the pilot, not now):

| Criterion | Proposal | Note |
|---|---|---|
| Primary | Within the human annotators' range against gold, and ≥ 0.85 macro-F1 if human agreement allows it | An absolute 0.85 above human agreement is unattainable by construction |
| No hidden dimension | Each core dimension separately meets the same criterion | TC §15 |
| Accepted adverse precision | ≥ 0.95, one-sided 95% lower bound ≥ 0.90, at directional coverage ≥ 0.50 of eligible representative cases | §3.1 sample-size note |
| Calibration | Post-calibration top-label ECE ≤ 0.05, adverse-class reliability inspected | §5.5 |
| Sector stability | No adequately powered sector below the frozen tolerance; underpowered sectors stay unapproved | |
| Provenance | Every accepted record carries valid spans and scope | |
| Robustness | No prespecified semantics-preserving perturbation flips more than the frozen share of labels | §5.8 |

### 5.5 Probabilities and calibration

- **Store the full probability vector** for every arm that has one, with the ordered label list.
- **Metrics:**
  - multiclass Brier score, summed over classes and **not** divided by K (Gneiting & Raftery 2007 for proper scores);
  - log loss, with a fixed clipping ε;
  - top-label ECE with **15 equal-mass bins** (frozen), plus classwise ECE, since ECE is binning-sensitive and its plug-in estimate is biased (Kumar, Liang & Ma 2019; Nixon et al. 2019);
  - reliability curves per dimension and for the adverse class.

  Brier mixes calibration and discrimination, so it is never reported as pure calibration (TC §16).
- **Calibrator:** temperature scaling (Guo et al. 2017) fitted on `cal` only. Raw and calibrated results are both reported. Per-sector calibrators are not fitted in v1 (overfitting risk at these sizes).
- **Vendor "confidence" is not a probability.** TC §5 says Jev's documented confidence for a K-option choice is `(p_max − 1/K)/(1 − 1/K)`. The 2026-10-03 source check corroborated (via snippets) that the vendor distinguishes confidence from the selected probability, but **did not find that formula**. It is UNVERIFIED until someone reads the vendor page. Either way, vendor confidence, raw `p_max`, calibrated `p_max`, margin and entropy are **different statistics**. The routing score is one of them, named in the pre-registration.
- **Generative "confidence: 0.9" fields** are predictions to be validated, not probabilities. Verbalized confidence tends to be overconfident (Xiong et al., ICLR 2024). Where an arm exposes no label probabilities, its calibration is "unavailable", and only a separately validated routing score may be used.

### 5.6 Selective prediction

- Risk–coverage curves per dimension, with AURC (Geifman & El-Yaniv 2017; max-softmax baseline, Hendrycks & Gimpel 2017).
- **Threshold selection:** on `cal` only. Choose the routing threshold that maximizes directional coverage subject to accepted adverse precision ≥ 0.95. Freeze it, and never revise it on the sealed test.
- **At each candidate threshold, report:**
  - automated directional coverage;
  - accepted-set accuracy;
  - adverse precision and recall;
  - calibration of the accepted set;
  - cost;
  - fallback rate.
- **Selection changes the economic sample** (TC §16). Every downstream dataset carries routing and missingness indicators, and the analysis reports whether abstentions cluster in small, stressed or particular-sector issuers.

### 5.7 Cascade evaluation

Configurations, all frozen on `cal`:
1. cheap arm alone;
2. frontier arm alone;
3. cheap → frontier;
4. cheap → human;
5. cheap → frontier → `unresolved`.

- **The fallback is scored on the cases actually routed to it.** Its overall benchmark accuracy is never substituted (TC §17).
- Errors of the two models are not assumed independent. The report shows the joint error table on routed cases.
- **Cost:**
  - per 1,000 pairs;
  - per accepted supported feature;
  - per correct adverse label;
  - human-review hours.

  All measured from logged usage (prospective spec §6), never from list prices alone.
- **Latency:** median, p90, p95 and p99 under fixed concurrency. This is secondary for research batches.

### 5.8 Robustness, negative controls, error taxonomy

- **Name–definition binding test** (mandatory for arms 6–8). Swap which label **name** is bound to which **definition**, holding everything else fixed, and measure label flips against a test–retest floor. A preprint reports that a typed decision head followed the option name rather than the rubric bound to it: 32.5% yes/no flips on 1,200 questions, against a 1.33% test–retest floor, with type errors staying at 0% (arXiv 2609.26758, Sept 2026, verified via abstract only). If this replicates on our taxonomy, the label names themselves carry the measurement, and the written definitions are not doing the work they are meant to do.
- **Semantics-preserving perturbations** (labels should not move):
  - option-order permutation;
  - paraphrased instructions;
  - equivalent date formats;
  - passage order reversed with chronological tags kept;
  - irrelevant paragraphs inserted;
  - longer context;
  - repeated boilerplate;
  - issuer name masked.

  Report label flips and probability shifts. TC §6 notes the vendor itself documents sensitivity to choice order and irrelevant context.
- **Semantics-changing perturbations** (labels should move as the rules require):
  - negating a claim;
  - changing the segment;
  - substituting the comparison period;
  - removing the supporting evidence;
  - reversing a matched trend.
- **Negative controls:**
  - random labels: generalizable association should vanish;
  - same document on both sides: `unchanged` only where the topic is sufficiently discussed, else `not_discussed`.

  Shuffled issuers and shuffled periods are interpreted against their exact hypothesis, never as "must collapse" (TC §18).
- **Error taxonomy, for every error on `dev` and `cal`:**
  - negation;
  - temporal confusion;
  - entity confusion;
  - numeric context;
  - scope;
  - industry language;
  - hedging;
  - false certainty;
  - contradiction;
  - ambiguous gold.

## 6. Deterministic baselines (permanent controls)

These are computed for every pair and kept forever as the controls that any semantic feature must beat in stages 3, 5 and 6. All are pure functions of the two archived, normalized sections. Thresholds come from config, and every feature has a `feature_version`.

| Feature | Definition (frozen in the feature spec that implements it) |
|---|---|
| TF-IDF cosine similarity | Cosine of TF-IDF vectors of the two sections. IDF fitted in-fold on training documents only. |
| Raw cosine similarity | Cosine of term-count vectors (Lazy Prices-style). |
| Jaccard / edit-based similarity | Word-set Jaccard and normalized edit distance, as robustness siblings. |
| Changed-sentence ratio | Share of current sentences with no match (similarity below a threshold) in the previous section. |
| Inserted-sentence ratio | Current sentences unmatched / current sentence count. |
| Deleted-sentence ratio | Previous sentences unmatched / previous sentence count. |
| Section length change | log(words_t / words_{t−1}). |
| Financial dictionary features | Loughran–McDonald category proportions (negative, positive, uncertainty, litigious, constraining, weak/strong modal) per section and their change. The dictionary release is frozen and its license checked. |
| Risk Factors additions | Count and share of new risk-factor headings or paragraphs in Item 1A relative to the prior filing (10-K; 10-Q where updated). |
| MD&A additions / deletions | Inserted and deleted sentence counts and words within MD&A, and per MD&A subsection where the parser can split them. |
| Topic keyword presence change | Appearance or disappearance of each dimension's frozen term list. Feeds `not_discussed` baselines. |

**A semantic model is interesting only if it adds incremental value beyond these:** in stage 3 through arm 2, and in stages 5–6 through specification B.

## 7. Pretrained-model contamination: evidentiary weights

| Evidence | Weight | Why |
|---|---|---|
| Historical human-label accuracy of a model (stage 3) | Useful | Judged against human labels on supplied text. Still not proof of uncontaminated generalization (TC §13). |
| Historical economic regressions using **human** labels | Useful | Tests the concept, independent of any model |
| Historical economic or return tests using **modern model** labels | **Exploratory only** | The model may have seen later filings, outcomes or commentary. Look-ahead bias in pretrained models has been measured directly, and date instructions do not remove it (Sarkar & Vafa 2024; Lopez-Lira, Tang & Zhu 2025; Glasserman & Lin 2023 on the related distraction effect). |
| Point-in-time ("chronologically consistent") models | Stronger historically | Trained only on text before a vintage date (He, Lv, Manela & Wu 2025). Weaker models; a candidate arm for historical economic tests. |
| Prospective frozen outputs (prospective spec) | **Strongest** | Generated before the outcomes exist, never regenerated |

## 8. Economic validation stream (stage 5)

**Principle:** test whether the concept predicts the economic variable it names, before any return. Use an interpretable regression first. LightGBM or XGBoost come only after a linear effect is established or clearly ruled out, under the same splits and budget.

### 8.1 First hypothesis (E1): demand deterioration → next-quarter revenue-growth deceleration

- **Unit:** issuer-quarter t with a sufficient D1 observation (primary mode and basis from taxonomy v1).
- **Outcome:** `Δg_{t+1} = g_{t+1} − g_t`, where `g` = log year-over-year quarterly revenue growth computed from **as-filed** XBRL revenue. `g_t` comes from filing t. `g_{t+1}` comes from filing t+1, known at its acceptance. No restated values; conflicting facts are withheld (EP P13).
- **Specifications**, on identical samples and splits:
  - **A, fundamentals:** `g_t`, `g_{t−1}`, log size, sector × calendar-quarter fixed effects.
  - **B = A + deterministic text:** §6 features for the MD&A pair.
  - **C = B + semantic:** indicators for D1 `deteriorated` and `improved` (reference: `unchanged`), plus evidence-status indicators (`not_discussed`, `mixed`, `incomparable`) so that missingness is modeled, not dropped.
- **Hypothesis:** in C the `deteriorated` coefficient is < 0, one-sided. Two-way clustered SEs (firm, quarter).
- **Incremental test:** walk-forward out-of-sample MSE of C vs B, with a nested-model forecast comparison test (named in the pre-registration).
- **Two versions, in order:**
  - **E1-H** uses **human gold labels** on the annotated benchmark sample. It tests the concept and is power-limited by sample size.
  - **E1-M** uses accepted model labels on the full universe. It is exploratory historically (§7), and confirmatory only on prospective data.
- **Timing caveat:** the earnings release usually precedes the 10-Q, so `g_t` and part of the narrative are public before the filing (TC §20). This affects returns, not E1, but it is recorded.

### 8.2 Later targets (each a separate pre-registration, after E1)

| Feature | Target | Business-model caveat (TC §19) |
|---|---|---|
| D2 inventory pressure ↑ | Next-quarter inventory write-down disclosure, change in inventory/sales, gross-margin change | Margins can move on input costs, not inventory |
| D3 liquidity pressure ↑ | Next-quarter operating cash flow, new financing, covenant events, going-concern opinion | Working-capital release can improve cash flow temporarily |
| Semantic change | Analyst revisions | **Only** if a timestamped point-in-time consensus source exists. None does in the repo (CP X5). Its absence does not block E1. |

## 9. Return validation (stage 6)

### 9.1 Preconditions (all required)

1. Stage 3–4 gates are passed for the feature's dimension and mode.
2. Calibration and routing are acceptable on the sealed test.
3. The feature shows incremental economic information in stage 5 (E-H, and E-M where applicable).

### 9.2 Design

- **One primary horizon, chosen before testing.** Recommendation: the next **one-month** total return from the first rebalance at or after the feature's `known_at`. This matches ADR 0006's monthly cadence, so a result is something the system could actually trade. Every other horizon (5, 20, 60, 120 sessions) is secondary and Holm-adjusted.
- **Primary metric:** the monthly cross-sectional Spearman rank IC within the ADR 0006 universe, with Newey–West inference on the monthly series.
- **Secondary metrics:**
  - quantile monotonicity;
  - top-minus-bottom and top-minus-universe returns (long-only is the deployable side);
  - turnover;
  - net of the frozen cost model;
  - drawdown;
  - sector and period stability;
  - feature ablations.
- **Specifications** with an identical downstream budget (same model class, same tuning grid, same splits):
  - **A:** market + fundamentals.
  - **B:** A + deterministic text.
  - **C:** B + typed-classifier features.
  - **D:** B + generative features.
  - **E:** B + actual cascade outputs with routing and missingness indicators.
- **Learned combination** (Elastic Net, then gradient boosting) only after a single-feature effect is shown, and it outputs a **ranking score, never an order** (INT §Stage 4).

### 9.3 Where the bar sits

A feature is promoted to portfolio research only if C, D or E beats B out of sample under Holm, net of costs, stable across period blocks, with the effect at or below the publication-decay prior stated in its pre-registration. **Classifier accuracy is never sufficient.**

### 9.4 Holdout

Each return hypothesis registers its own holdout. For model-derived features the holdout is **prospective**: filings after the configuration freeze. H1's holdout (2024-01-01 → 2026-09-30) has already been viewed in the market sense. A new hypothesis's file must state, as the backtest spec requires, what was seen of its holdout period.

## 10. Research registry

The store's registry (`hypotheses`, `trials`, `trial_results`; schema v3) records **backtest** trials with in-sample, holdout and tracking kinds. It has no fields for dataset, feature-set, taxonomy or model versions, hyperparameters, seeds or train/validation/test windows, and it cannot represent a classifier benchmark or a regression. **Recommendation:** a spec for an append-only `research_experiments` registry, in a research store separate from the runtime store, with one row per experiment run:

| Field | |
|---|---|
| `experiment_id`, `family`, `hypothesis_ref` | family counts trials as the backtest registry does |
| `stage` | 3–7 |
| `confirmatory` | bool, plus a pre-registration link and hash |
| `dataset_version`, `sampling_frame_hash` | |
| `feature_set_version`, `taxonomy_version` | |
| `model_type`, `model_provider`, `model_id_requested`, `model_id_observed`, `model_version` | |
| `prompt_version`, `calibration_version`, `routing_policy_version` | |
| `hyperparameters_json`, `random_seed` | |
| `train_window`, `validation_window`, `test_window` | dates and split ids |
| `primary_metric`, `primary_value`, `primary_ci` | |
| `secondary_metrics_json` | |
| `holdout_status` | `unspent` / `spent` / `repeat` (flagged) |
| `outcome` | `ok` / `failed` / `refused` / `abandoned`, plus a message |
| `code_version`, `code_dirty`, `started_at`, `finished_at`, `run_by` | |

Rows are never deleted or updated. Failed and abandoned runs stay. Until that spec is accepted, experiments that need registration **wait**. The registry is not replaced by an ad-hoc file without an owner decision (the trial-registry note says the registry lives in the store).

## 11. Insider-purchase stream

**Prior evidence** (G2, graded on main):
- Buying liquid stocks at or after Form 4 acceptance, held for days: **NOT SUPPORTED** since 2010 (G2-A).
- A monthly, filing-timed portfolio of opportunistic or clustered purchases in non-microcaps: **MIXED**, with only pre-2008 gross support (G2-B).
- Lakonishok & Lee (2001) find purchases informative and sales not, driven by small firms, on 1975–95 data.
- Cohen, Malloy & Pomorski (2012) find opportunistic trades earn about 82 bp/month value-weighted abnormal, routine trades about 0. That is historical and gross, so it does not establish a TradePartner effect (CP X3).

**Data:** the draft [event-data spec](../specs/event-data.md) (#306) Form 4 collector. Events are `transaction_code = P` (SEC: "open market or private purchase") **and** acquired/disposed = `A`:
- `transaction_date` is stored apart from availability;
- `known_at` is the EDGAR acceptance timestamp in UTC from submissions (EP P12), never the filing date;
- 10b5-1 and other deemed execution dates are kept as reported;
- the Section 16 deadline is the end of the second business day after execution (Rule 16a-3(g)).

The CMP routine/opportunistic classification needs 3 prior calendar years per insider (G2), so history starts at least 3 years before the test start.

**Ordered tests** (fixed-sequence gatekeeping: each test is confirmatory only if every earlier one rejected; all of them are registered):

1. Binary purchase month → next-month return (primary horizon as §9.2) within the ADR 0006 universe.
2. Abnormal purchase intensity: value relative to the insider's own history, market cap and holdings.
3. Insider role: CEO/CFO vs director vs 10% owner.
4. Clustered purchases: distinct insiders within a frozen window.
5. Context interactions: drawdown, valuation, earnings, liquidity, market cap, and **disclosure state** (taxonomy features, only after stage 5 passes for that dimension).

Each test states a prior centered near zero, as CP HS-2 does. A failed step 1 does not stop exploratory analysis of steps 2–5, but those results are labeled exploratory.

## 12. Conditional PEAD stream

**Question:** under what observable conditions is earnings information incorporated slowly **today**? Not whether PEAD "exists".

**Prior evidence** (handoff H3 graded PEAD NOT SUPPORTED for the investable, non-microcap universe; the 2026-10-03 citation check agrees):

| Condition | Grade for today's tradable case | Basis |
|---|---|---|
| Unconditional SUE drift, US non-microcaps after ~2006 | **NOT SUPPORTED** | Martineau (CFR 2022): no drift in large stocks since 2006. Meursault et al. (JFQA 2023) call classic PEAD close to 0 recently. |
| Illiquid / high arbitrage risk | Historically present. **NOT SUPPORTED** as tradable: costs take 70–100% of paper profits. | Chordia et al. (FAJ 2009); Mendenhall (J. Business 2004) |
| Attention: same-day announcement congestion | **MIXED** (pre-2006 evidence; not re-tested after 2006 in this check) | Hirshleifer, Lim & Teoh (JF 2009) |
| Attention: Friday announcements | **MIXED**: an original finding, then a selection-bias correction that removes it | DellaVigna & Pollet (JF 2009) vs Michaely, Rubin & Vedrashko (JFE 2016) |
| Information uncertainty, low coverage, revenue surprise | **INSUFFICIENT** for today (pre-2006 Tier 1 only) | Zhang (JF 2006); Jegadeesh & Livnat (JAE 2006) |
| Text-based earnings surprise | **INSUFFICIENT**: one peer-reviewed study, no replication found, costs not checked. The only modern positive lead, and directly relevant to the semantic program. | Meursault, Liang, Routledge & Scanlon (JFQA 2023) |

**Data availability, which decides what can be tested honestly:**

| Variable | Point-in-time source | Status |
|---|---|---|
| Announcement time | 8-K Item 2.02 acceptance (EDGAR, UTC) | Feasible (event-data spec) |
| **Earnings surprise at the announcement** | EPS lives in the release exhibit (unstructured HTML). XBRL values arrive only with the later 10-Q/10-K. | **Gap.** An XBRL-based SUE is known only at the 10-Q filing date, which is a different event. Reading the release needs a deterministic table parser (feasibility unknown); LLM extraction is barred by ADR 0008. |
| Market cap, liquidity, announcement volume and return, momentum | Prices | Available |
| Earnings-day congestion | Count of 8-K 2.02 filings per session | Feasible |
| Institutional ownership | 13F (quarterly, 45-day lag) | Needs an ingest |
| Analyst coverage, dispersion, revisions | I/B/E/S, FactSet or LSEG | **Unavailable**; price unknown (CP X5) |
| Guidance revision, semantic disclosure change | The semantic program | After stage 5 only |

**Design:**
- One primary horizon, recommended **60 sessions starting the session after `known_at`** of the surprise measure.
- The event's tradability is checked against the monthly cadence; a monthly-rebalance version is secondary.
- The primary confirmatory family is limited to **three** conditions with available data and the strongest prior: earnings-day congestion, liquidity tercile within the universe, and announcement volume. Holm applies across them.
- Every other condition is exploratory.
- If the surprise gap above cannot be closed deterministically, the stream is **reframed as a 10-Q-date event** and labeled as such, or parked. It is never filled by an LLM.

## 13. Stop conditions (any one stops, narrows or rejects a branch; each is recorded as a negative result)

- Humans cannot label it reliably (protocol §8).
- Semantic arms do not beat arms 2–4 (H-M family fails).
- Calibration is unusable after the frozen calibrator.
- Confidence does not separate hard from easy cases: no risk–coverage advantage over random abstention.
- Routing sends most cases to the fallback, so the cost advantage disappears.
- Economic variables are not predicted (stage 5 fails).
- Incremental information disappears after controls (C ≯ B).
- Results collapse under minor methodological changes (prespecified sensitivity set).
- Realistic costs eliminate the effect.
- Data quality or licensing prevents honest point-in-time testing (for example the PEAD surprise gap, or analyst data).
- Model-version changes materially alter classifications (prospective spec §8).

## 14. Unresolved questions

1. Does ADR 0008 cover non-generative hosted models (Jev) and local pretrained encoders? This brief assumes yes until the owner says otherwise.
2. Research environment and store: where do research-only code, dependencies and outputs live so that ADR 0008 point 2's isolation tests can be extended to them?
3. The research spend ceiling for annotation and model calls.
4. The sealed test period and the split seed: to be fixed in the stage-3 pre-registration.
5. Whether as-filed income-statement XBRL facts (revenue, inventory, operating cash flow) are in the store (CP §4 "Partly?"), which E1 depends on.
6. Earnings-release parsing feasibility for conditional PEAD.

## 15. Acceptance criteria for this brief

- The owner accepts the stage gates (§2) and the governance table, or amends them through this PR.
- Every number this brief marks as a proposal is either confirmed or replaced in the relevant pre-registration before that stage's data are seen.
- `spec-critic`, or an equivalent adversarial review, finds no untestable criterion. That is recommended before acceptance, because this document will govern later specs.

## 16. Dependencies

Taxonomy v0 → pilot → taxonomy v1; the document corpus and section parser; the research-registry spec; the research-measurement-boundary ADR (for arms 5–9 and prospective capture); the event-data collectors (Form 4, 8-K 2.02); as-filed XBRL facts.

## 17. Risks

| Risk | Mitigation |
|---|---|
| Too few sealed-test adverse cases to prove precision | Power calculation after the pilot; challenge sample reported apart |
| Garden of forking paths across 9 arms × 3 dimensions × modes × horizons | One primary endpoint, a small Holm family, and everything else exploratory by label |
| Contaminated historical model results mistaken for evidence | §7 weights; prospective holdout for model features |
| Selective-labeling bias in economic tests | Routing and missingness indicators; the abstention-concentration report |
| Short price history (from 2016) | Report effective sample sizes; prefer stage-5 economic targets, which use XBRL from 2009+ |
| Registry gap delays everything | Write the registry spec early, in parallel with the pilot |

## 18. Recommended next step

The owner decides §14 items 1–3. Then open, in order:
1. a research-measurement-boundary ADR draft;
2. a research-registry spec;
3. a document-corpus spec (MD&A and Risk Factors sections, hashes, parser versions).

All three can proceed in parallel with the annotation pilot, which needs none of them except the corpus.

## Sources

Verified 2026-10-03 by a research agent at metadata level (publisher, index or abstract pages). Its fetches of several publisher pages were blocked, so the finding lines rest on abstracts, not full text.
- Avramov, D., Cheng, S. & Metzker, L. (2023). Machine Learning vs. Economic Restrictions. *Management Science* 69(5), 2587–2619. https://papers.ssrn.com/abstract=3450322
- Bailey, D. H. & López de Prado, M. (2014). The Deflated Sharpe Ratio. *J. Portfolio Management* 40(5), 94–107. https://papers.ssrn.com/abstract=2460551
- Benjamini, Y. & Hochberg, Y. (1995). Controlling the False Discovery Rate. *JRSS B* 57(1), 289–300.
- Bernard, V. & Thomas, J. (1989). *JAR* 27 (Suppl.), 1–36; (1990). *JAE* 13(4), 305–340.
- Byrt, T., Bishop, J. & Carlin, J. B. (1993). Bias, Prevalence and Kappa. *J. Clin. Epidemiol.* 46, 423–429.
- Cameron, A. C., Gelbach, J. & Miller, D. (2008). Bootstrap-Based Improvements for Inference with Clustered Errors. *REStat* 90(3), 414–427; (2011). Robust Inference with Multiway Clustering. *JBES* 29(2).
- Chen, A. Y. & Zimmermann, T. (2022). Open Source Cross-Sectional Asset Pricing. *Critical Finance Review* 11(2), 207–264. https://papers.ssrn.com/abstract=3604626
- Chordia, T., Goyal, A., Sadka, G., Sadka, R. & Shivakumar, L. (2009). Liquidity and the Post-Earnings-Announcement Drift. *FAJ* 65(4), 18–32.
- Cohen, J. (1960). A Coefficient of Agreement for Nominal Scales. *Educ. Psychol. Meas.* 20(1), 37–46.
- Cohen, L., Malloy, C. & Pomorski, L. (2012). Decoding Inside Information. *JF* 67(3), 1009–1043.
- DellaVigna, S. & Pollet, J. (2009). Investor Inattention and Friday Earnings Announcements. *JF* 64(2), 709–749.
- Feinstein, A. R. & Cicchetti, D. V. (1990). High Agreement but Low Kappa: I. *J. Clin. Epidemiol.* 43, 543–549.
- Geifman, Y. & El-Yaniv, R. (2017). Selective Classification for Deep Neural Networks. NeurIPS. https://arxiv.org/abs/1705.08500
- Glasserman, P. & Lin, C. (2023). Assessing Look-Ahead Bias in Stock Return Predictions Generated by GPT Sentiment Analysis. arXiv 2309.17322 (journal venue unverified).
- Gneiting, T. & Raftery, A. E. (2007). Strictly Proper Scoring Rules, Prediction, and Estimation. *JASA* 102(477), 359–378.
- Gu, S., Kelly, B. & Xiu, D. (2020). Empirical Asset Pricing via Machine Learning. *RFS* 33. https://nber.org/papers/w25398
- Guo, C., Pleiss, G., Sun, Y. & Weinberger, K. Q. (2017). On Calibration of Modern Neural Networks. ICML, PMLR 70. https://proceedings.mlr.press/v70/guo17a
- Gwet, K. L. (2008). Computing Inter-Rater Reliability and Its Variance in the Presence of High Agreement. *BJMSP* 61, 29–48.
- Hansen, P. R. (2005). A Test for Superior Predictive Ability. *JBES* 23, 365–380.
- Harvey, C., Liu, Y. & Zhu, H. (2016). …and the Cross-Section of Expected Returns. *RFS* 29(1), 5–68.
- He, S., Lv, L., Manela, A. & Wu, J. (2025). Chronologically Consistent Large Language Models. arXiv 2502.21206.
- Hendrycks, D. & Gimpel, K. (2017). A Baseline for Detecting Misclassified and Out-of-Distribution Examples. ICLR. https://arxiv.org/abs/1610.02136
- Hirshleifer, D., Lim, S. S. & Teoh, S. H. (2009). Driven to Distraction. *JF* 64(5), 2289–2325.
- Holm, S. (1979). A Simple Sequentially Rejective Multiple Test Procedure. *Scand. J. Statist.* 6(2), 65–70.
- Jegadeesh, N. & Livnat, J. (2006). Revenue Surprises and Stock Returns. *JAE* 41(1–2), 147–171.
- Krippendorff, K. (2018). *Content Analysis: An Introduction to Its Methodology* (4th ed.). SAGE; and "Computing Krippendorff's Alpha-Reliability", UPenn ASC working paper (year unverified). https://repository.upenn.edu/asc_papers/43. The α ≥ 0.800 / ≥ 0.667 guidance used in the protocol is attributed to Krippendorff; the page reference is still to be pinned.
- Kumar, A., Liang, P. & Ma, T. (2019). Verified Uncertainty Calibration. NeurIPS. https://arxiv.org/abs/1909.10155
- Lakonishok, J. & Lee, I. (2001). Are Insider Trades Informative? *RFS* 14(1), 79–111.
- López de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley, ch. 7. Purging and embargo, confirmed via a secondary description only.
- Lopez-Lira, A., Tang, Y. & Zhu, M. (2025). The Memorization Problem: Can We Trust LLMs' Economic Forecasts? SSRN 5217505.
- Martineau, C. (2022). Rest in Peace Post-Earnings Announcement Drift. *Critical Finance Review* 11(3–4), 613–646.
- McLean, R. D. & Pontiff, J. (2016). Does Academic Research Destroy Stock Return Predictability? *JF* 71(1).
- Mendenhall, R. (2004). Arbitrage Risk and Post-Earnings-Announcement Drift. *Journal of Business* 77(4), 875–894.
- Meursault, V., Liang, P. J., Routledge, B. & Scanlon, M. (2023). PEAD.txt: Post-Earnings-Announcement Drift Using Text. *JFQA* 58(6), 2299–2326.
- Michaely, R., Rubin, A. & Vedrashko, A. (2016). Are Friday Announcements Special? Overcoming Selection Bias. *JFE* 122(1), 65–85.
- Naeini, M. P., Cooper, G. & Hauskrecht, M. (2015). Obtaining Well Calibrated Probabilities Using Bayesian Binning. AAAI.
- Nixon, J. et al. (2019). Measuring Calibration in Deep Learning. CVPR Workshops. https://arxiv.org/abs/1904.01685
- Petersen, M. (2009). Estimating Standard Errors in Finance Panel Data Sets. *RFS* 22(1), 435–480.
- Romano, J. & Wolf, M. (2005). Stepwise Multiple Testing as Formalized Data Snooping. *Econometrica* 73(4), 1237–1282.
- Sarkar, S. K. & Vafa, K. (2024). Lookahead Bias in Pretrained Language Models. SSRN 4754678.
- SEC Form 4 instructions (transaction code P) and Rule 16a-3(g) deadline: https://www.sec.gov/files/form4.pdf ; https://www.sec.gov/edgar/searchedgar/ownershipformcodes.html (confirmed via search extract; PDF fetch blocked).
- White, H. (2000). A Reality Check for Data Snooping. *Econometrica* 68(5), 1097–1126.
- Xiong, M. et al. (2024). Can LLMs Express Their Uncertainty? ICLR. https://arxiv.org/abs/2306.13063
- Zhang, X. F. (2006). Information Uncertainty and Stock Returns. *JF* 61(1), 105–137.

**Not verified in this pass:** the Subrahmanyam (2025) reconciliation cited in the handoff; a post-2006 re-test of the distraction effect; any replication of PEAD.txt.
