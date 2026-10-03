# Financial Disclosure Annotation Protocol (pilot, taxonomy v0)

**Brief:** #618  ·  **Date:** 2026-10-03  ·  **Status:** DRAFT. Its numbers and rules are frozen, with this file's git blob hash recorded, **before the first pilot label is written**. Any later change is a new version, and labels made under the old version stay labeled with it.  ·  **Agent/model:** main session (research lead)

**Program step:** **Label.** It measures whether humans can label [taxonomy v0](2026-10-03-semantic-taxonomy-v0.md) reliably. No model is involved, and no model output is shown to anyone.

**Companion artifacts (#618):** [taxonomy v0](2026-10-03-semantic-taxonomy-v0.md) · [evidence review](2026-10-03-disclosure-change-evidence-review.md) · [ML validation brief](2026-10-03-ml-validation-methodology-brief.md) · [prospective feature spec](2026-10-03-prospective-semantic-feature-spec.md).

---

## 1. Objective

Find out, per dimension and per measurement mode, whether independent trained annotators agree on the evidence status and direction of D1 demand, D2 inventory pressure and D3 liquidity pressure, and do so on the **adverse classes**, not just on `unchanged` and `not_discussed`. Also estimate annotation time per pair, so the budget for the 2,000-pair benchmark can be set from data.

## 2. Scope

- **In:** about 200 document pairs (within the 150–250 range in the program plan); D1–D3; both modes (`cross_document`, `narrated`); total-company scope as the primary unit, with segment observations recorded descriptively; MD&A only (taxonomy §2).
- **Out:** model benchmarking; Risk Factors, earnings releases and calls; the cross-document relation task, which is designed in §9 but piloted separately afterwards; any return or fundamentals outcome.

## 3. Roles and independence

| Role | Who | Sees | Never sees |
|---|---|---|---|
| Annotators **A** and **B** | Two people with accounting or financial-analysis training, recruited for this pilot | The frozen manual; for each pair, both MD&A sections in full, issuer name, form, period ends and filing dates, and the D2 applicability flag | Other annotators' labels; any model output; deterministic text features; the pair's sampling stratum; later filings, prices or news (§6) |
| Annotator **C** (third, independent) | A third trained person | Same as A and B, on a prespecified random 25% subset (§4.4) | Same |
| **Adjudicator** | The owner, or a fourth person (**owner decision**) | All three raw labels, after every independent label is locked | Model outputs, outcomes |
| Corpus builder | Agent or owner, with a deterministic script | Sources and hashes | Labels, until the pilot closes |

Rules:
- **No LLM or other model is an annotator, adjudicator or tie-breaker.** Gold labels are human decisions (ADR 0008 point 3 allows a human decision as a source). A model-assisted label would make the later benchmark circular.
- **The owner should not be annotator A, B or C.** The owner designs the hypotheses, and an annotator who knows them can label toward them. The owner as adjudicator is acceptable if each adjudication carries a written reason and raw labels are preserved. **Owner decision.**
- Raw independent labels are **never edited**. Adjudication writes new records that point to the raw ones.

## 4. Sampling

### 4.1 Frame

The frame is every eligible (issuer, current filing, previous filing) pair where:
- the issuer is in the ADR 0006 universe **as of the current filing's acceptance time**, read through the as-of API, not from today's membership;
- the issuer is not in the financial sector (SIC 6000–6799);
- both filings are 10-Q or 10-K originals, not amendments, with an MD&A that the section extractor finds;
- the current filing was accepted between **2017-01-01 and 2025-12-31**.

Amendments are excluded from v0 and logged as a future stratum. The frame is built and counted by a deterministic script before sampling, and its row count per stratum is recorded.

### 4.2 Two samples, reported separately (TC §12)

| Sample | Size | Purpose |
|---|---|---|
| **R, representative** | 140 pairs | Agreement as it would be on the intended distribution. Known inclusion probabilities. |
| **C, challenge** | 60 pairs | Enough adverse and hard cases to estimate class-level agreement. Enrichment changes class priors, so **calibration-type statistics from C never describe R**. |

### 4.3 Strata

**R, by pair type** (fixed counts; within each type, allocation is proportional to the frame across period block × market-cap tercile):

| Pair type | Count | Note |
|---|---|---|
| 10-Q vs preceding 10-Q (Q2 vs Q1, Q3 vs Q2), `sequential` | 50 | |
| 10-Q vs same-quarter 10-Q a year earlier, `year_over_year` | 40 | |
| 10-K vs prior 10-K, `year_over_year` | 30 | |
| Q1 10-Q vs preceding 10-K | 20 | Measures the cost of taxonomy §4.3's form-mismatch rule |

- **Period blocks:** 2017–2019; 2020; 2021–2022; 2023–2025.
- **Market-cap terciles:** within the universe on the current filing's date.
- **Sector:** the store's classification at the filing date (SIC-based), reported but not forced. A sector with fewer than 10 sampled pairs is reported as underpowered.

**C, by enrichment trigger** (15 each). Every trigger uses **only information known at the current filing's acceptance**:

| Trigger | Rule (thresholds fixed before sampling) |
|---|---|
| Demand-adverse | Deterministic match on a frozen demand-weakness phrase list in the current MD&A, **or** a year-over-year revenue decline in as-filed XBRL facts known at acceptance |
| Inventory | Phrase list (excess, obsolete, write-down, destock, markdown) **or** a year-over-year rise in the inventory-to-revenue ratio above the threshold, as-filed |
| Liquidity | Phrase list (going concern, waiver, covenant, substantial doubt, refinancing) **or** negative trailing operating cash flow, as-filed |
| Hedge / ambiguity | Current MD&A has a frozen hedge-phrase list hit ("despite", "moderation", "selected", "resilient", "softer") near a dimension term, **and** cosine similarity to the previous MD&A is in the frame's lowest quintile |

Lexical triggers favor lexical systems on C. That is why C is reported apart and never enters the benchmark's primary endpoint ([ML brief §5](2026-10-03-ml-validation-methodology-brief.md#5-semantic-benchmark-statistical-plan-stages-34)).

**Concentration caps:** at most 2 pairs per issuer across R and C, and no filing in more than one pilot pair.

### 4.4 Randomness, recorded in advance

- One seed is published as a comment on #618 **before** the frame is built. It drives the stratified draw, the 25% subset for annotator C (50 pairs: 35 from R and 15 from C, drawn within sample) and the presentation order, which is independently shuffled per annotator.
- Inclusion probabilities are stored per pair.
- **Pilot issuers are burned for evaluation:** all of them go to the development split of the later benchmark and never into its sealed test ([ML brief §3](2026-10-03-ml-validation-methodology-brief.md#3-splits-leakage-and-dependence)).

### 4.5 Corpus build

- Raw filing documents are fetched once from EDGAR Archives under the SEC fair-access limits, and stored with sha256 of the raw bytes.
- MD&A is extracted by a **deterministic, versioned** section parser (`parser_version` recorded). Text is normalized deterministically (whitespace, HTML entities), and character offsets refer to the normalized text, whose sha256 is recorded too.
- A section-extraction failure is a pair with status `extraction_failure` for all its observations. It is counted, not silently replaced.
- The store holds no filing bodies today, so the pilot corpus needs either a disposable `spike/` build, per [git-workflow.md](../ways-of-working/git-workflow.md) with findings written up here, or a size-S/M issue. A production document-ingest belongs to a separate spec (the integration note's `filing_documents` / `filing_sections`). **Owner decision** on which.

## 5. The annotation manual

The manual is the taxonomy v0 document plus the following. It is **frozen before pilot labeling**, and its hash goes into every label record.

1. **Decision procedure per observation:**
   - Check applicability.
   - Find the evidence in each document.
   - Check that entity, scope, period and basis match (taxonomy §4.3).
   - Assign the evidence status.
   - Assign a direction only if the status is `sufficient`.
   - Mark the exact spans.
   - Record confidence and an optional note.
2. **Order of work per pair:**
   - Read both sections in full.
   - Label `narrated` for the current document.
   - Label `cross_document` for the pair.
   - Work D1, then D2, then D3.
   - Do not revise an earlier dimension because of a later one.
3. **Real examples:** for each dimension, at least 3 positive, 3 negative, 3 ambiguous and 2 counterexamples, quoted from **filings outside the pilot frame** (accepted before 2017). They replace the taxonomy's constructed examples. Each one carries its accession number and offsets.
4. **Intensifier and hedge list:** which word shifts count as directional (taxonomy rule 6). It starts empty except for scope narrowing and conceded weakness, both of which count. The practice round extends it, and it freezes with the manual.
5. **Hard-case log:** each ambiguous example in the taxonomy (for example "resilient despite moderation"; precautionary revolver draws) has a written rule or an explicit "annotator judgment, flag low confidence".
6. **Practice round:**
   - 20 practice pairs from pre-2017 filings, outside the frame.
   - Every annotator labels them independently, then all discuss with the adjudicator.
   - The manual is amended, and then frozen.
   - Practice labels are kept but **never** enter the pilot statistics.

## 6. Blinding and hindsight controls

- Annotators receive only the two archived sections and the metadata in §3. They are instructed not to look up the company, its prices, later filings or news while labeling, and they sign that instruction.
- **Hindsight check:** after labeling each pair, the annotator answers "Do you know or recall what happened to this company in the year after the current filing? yes/no". Agreement and the adverse-label rate are reported by that answer. A material difference is a finding to record, not to correct.
- Issuer names cannot be removed from MD&A without damaging the text, so they are **not** masked in the pilot. The cost is the hindsight risk above, and the check is how it is measured.
- Annotators do not know which pairs came from the challenge sample.

## 7. Record and provenance (per label)

Every label carries the taxonomy record fields ([taxonomy §8](2026-10-03-semantic-taxonomy-v0.md#8-record-layout-every-labeled-observation)), plus:

| Field | Meaning |
|---|---|
| `label_id` | Unique. |
| `annotator_id` | A pseudonym. The pseudonym-to-person mapping stays with the owner, outside the repo. |
| `round` | `practice` / `independent` / `third` / `adjudication`. |
| `manual_version`, `manual_sha256` | |
| `labeled_at` | UTC, tz-aware. |
| `seconds_spent` | Per pair, from the tool. |
| `annotator_confidence` | `low` / `medium` / `high`. |
| `hindsight_known` | yes/no (§6). |
| `note` | Free text. |
| `adjudicates` | For an adjudication record: the raw `label_id`s it resolves, plus `adjudication_reason`. |
| `sample`, `stratum`, `inclusion_prob` | Joined in at analysis time only; never shown to annotators. |

**Storage.** Labels are research data, not runtime facts. They live in an **append-only, versioned research dataset outside the runtime store**: the location and format are an owner decision, and it may become the research registry from the [ML brief §10](2026-10-03-ml-validation-methodology-brief.md#10-research-registry). The rules: no row is updated in place; each export has a content hash; analysis code reads only exports.

**Tooling.** Character-offset span selection needs an annotation tool. Candidates are open-source span-annotation tools. Choosing one follows [ADR 0004](../decisions/0004-tooling-adopt-avoid.md)'s adopt/avoid process. A spreadsheet is acceptable only if spans are recorded as offsets and checked by a script.

## 8. Agreement analysis plan (prespecified)

**Unit of analysis:** one total-company observation (pair × dimension × mode). Segment observations are reported descriptively only.

**Label variables analyzed, per dimension × mode:**

| Variable | Classes | Statistic(s) |
|---|---|---|
| **J, joint label** | `improved`/`increased`, `unchanged`, `deteriorated`/`decreased`, `not_discussed`, `mixed`, `incomparable`. `extraction_failure` is reported as a rate and excluded. | Raw agreement; Cohen's κ (A vs B); Krippendorff's α (nominal), for A+B on all observations and A+B+C on the subset; 6×6 confusion matrix; prevalence per class |
| **S\*, sufficiency** | `sufficient` vs not | α (nominal), raw agreement |
| **D, direction** | 3 classes, on observations both annotators marked `sufficient` | α nominal **and** α ordinal (the classes are ordered); confusion matrix |
| **V, adverse** | adverse native label vs anything else, on all observations | Specific (positive) agreement 2a/(2a+b+c); raw agreement; count of observations where ≥1 annotator chose adverse |
| **Per-class specific agreement** | each class of J | 2·n_kk / (n_k· + n_·k) |
| **Span overlap** | spans for observations agreed as `sufficient` with the same direction | Token-level F1 between annotators |

**Diagnostics, never gates:**
- PABAK, and the prevalence and bias indices, to show when κ is depressed or inflated by skewed prevalence (the "kappa paradoxes").
- A comparison of α(A,B) with α(A,C) and α(B,C) on the subset, to find an idiosyncratic annotator.

**Confidence intervals:** issuer-cluster bootstrap, 2,000 resamples, percentile intervals. The seed is the published one.

**Majority-class guard.** Aggregate α is **never** the only gate. If the modal class of J holds more than 60% of observations, the report puts the per-class specific agreements beside α, and the class-level gates decide.

**Gates.** These are proposals, frozen with this document. Krippendorff's conventional guidance is the reference point: α ≥ 0.800 for reliable data, 0.667–0.800 for tentative conclusions only. The reference is under verification in the [ML brief sources](2026-10-03-ml-validation-methodology-brief.md#sources).

| Gate | Threshold | Applies to |
|---|---|---|
| G1 continue | α(J) ≥ 0.667, lower 95% bound reported | each dimension × mode, sample R |
| G2 reliable | α(J) ≥ 0.800 | each dimension × mode, sample R |
| G3 evidence first | α(S\*) ≥ 0.667 | each dimension × mode, R |
| G4 adverse | specific agreement on V ≥ 0.70 **and** ≥ 20 observations with ≥1 adverse vote | each dimension × mode, R ∪ C (enrichment allowed here; reported per sample too) |
| G5 operational | `extraction_failure` rate ≤ 5% of pairs | the corpus build |

**Decision table:**

| Result | Action |
|---|---|
| G1–G4 pass, G2 pass | Dimension × mode is eligible for freezing in taxonomy v1. |
| G1, G3, G4 pass, G2 fails | Eligible as "tentative". Benchmark results on it are reported as such. |
| G4 has fewer than 20 adverse observations | **Underpowered**, not passed. Label one prespecified extension batch of up to 50 pairs (the 250-pair ceiling), drawn from C's triggers with the same seed stream. |
| G1 or G3 or G4 fails | Revise the definition (taxonomy §11). Re-test on **fresh** pairs, at most two rounds. Then narrow or drop it, and record the negative result. |
| G5 fails | Fix the parser before any agreement conclusion. Parser failures are not annotator disagreement. |

A dimension that passes in one mode only is frozen in that mode only.

**What is reported, always:**
- every statistic above with its denominator;
- confusion matrices;
- agreement by period block, cap tercile, pair type and sector (exploratory);
- by-hindsight agreement;
- median and p90 seconds per pair;
- the full disagreement log;
- the list of manual changes, if any were made after freezing (each change makes a new version).

## 9. Separate task: cross-document relation labels

This is designed here and piloted **after** D1–D3, on its own pairs. Ordinary change is not contradiction (TC §11).

- **Input:** two aligned spans with explicit direction: *premise* = the earlier or primary source, *hypothesis* = the later or secondary one, plus their metadata.
- **Preconditions checklist**, each yes / no / unclear:
  - same entity (and same segment);
  - same period, or the same fixed future period;
  - same scope (measure, geography, product);
  - same conditions (assumptions, "excluding X").
- **Labels:**

| Label | Rule |
|---|---|
| `entailed` | The hypothesis follows from the premise. |
| `compatible` | Both can be true together, including ordinary change across **different** periods ("strong in Q1", "weak in Q2"). |
| `tension` | Both can be true, but only under an unstated reconciliation (for example "no significant destocking" in the 10-Q and "customer destocking weighed on results" in the same-quarter call, with the scope unclear). |
| `incompatible` | **Only if all four preconditions are "yes"** and both cannot be true. |
| `insufficient_evidence` | Any precondition is "unclear", or a span lacks the needed claim. |

- **Pairings, piloted separately:** 10-Q vs the same-quarter earnings release (8-K Item 2.02 exhibit); prepared remarks vs Q&A, **if** a licensed transcript source exists (none in the repo today); narrative vs the numbers stated in the same filing.
- **Gates:** as §8 (α on the 5-way label; specific agreement on `incompatible` and `tension` with ≥ 20 observations each). If `incompatible` turns out rare, the pilot reports that as a finding rather than relaxing the definition.

## 10. Workflow and illustrative budget

1. The owner decides annotators, adjudicator, tooling, corpus route and budget (§3, §4.5, §7).
2. Publish the seed. Build the frame and the corpus. Run the parser QA (G5).
3. Write the manual's real-example section from pre-2017 filings, then run the practice round (§5.6).
4. Freeze the manual and this protocol, and record their hashes on #618.
5. Independent labeling by A and B (all pairs) and C (the subset). Lock the labels.
6. Run the agreement analysis by a script, from the locked export only.
7. Adjudicate, keeping raw labels.
8. Write the pilot report as a new `docs/research/` file. The gate decisions per dimension × mode go to the owner.

**Illustrative effort.** These are planning numbers, not measurements; the practice round replaces them. A pair needs two MD&As read (often thousands of words each) and 6 observations labeled (3 dimensions × 2 modes). At **8–20 minutes per pair** per annotator:
- A and B: 200 pairs × 2 annotators ≈ 53–133 hours;
- C on 50 pairs ≈ 7–17 hours;
- practice, training and adjudication ≈ 20–30 hours;
- total ≈ **80–180 hours**. At an assumed $40/hour that is about $3,200–$7,200.

This is research spend. The charter's $0 ceiling covers spend **by the running system** (ADR 0005), but the owner should still decide it explicitly.

## 11. Unresolved questions

1. Who annotates, and at what cost? Owner as adjudicator or not? (§3)
2. Corpus route: a `spike/` build or an issue (§4.5)? Where the label dataset lives (§7)?
3. The D2 materiality threshold, the trigger phrase lists and the XBRL thresholds for C: drafted from pre-2017 filings, then frozen before sampling.
4. Whether issuer-name masking is feasible enough to try on a 20-pair side experiment (it would measure the hindsight effect directly).
5. A transcript source for the prepared-vs-Q&A relation task: none today. Licensing is unknown (CP tension X5 covers analyst data. Transcripts are a separate, open question).

## 12. Acceptance criteria for this protocol

- An owner-approved PR merges it; then the seed is published and the hashes are recorded on #618.
- Every gate in §8 is computable by script from the locked label export.
- Practice-round evidence (time per pair, manual changes) is reported before step 5 starts.

## 13. Dependencies

The taxonomy v0 owner review; the corpus build (§4.5); as-of universe membership and the classification read for the frame; XBRL facts for the D2 applicability flag and the C triggers (presence in the store to be checked); annotators.

## 14. Risks

| Risk | Mitigation |
|---|---|
| Annotators converge by talking to each other | Independence instruction; separate tool accounts; agreement is computed on locked raw labels |
| Fatigue on long MD&As | Order shuffled per annotator; time logged; agreement by position in the queue (exploratory) |
| The practice round over-fits the manual | Practice pairs are from before 2017 and outside the frame |
| Enrichment triggers define the adverse class | C is reported apart; triggers are never shown to annotators |
| Too few adverse cases in R | The G4 pools R ∪ C, with per-sample reporting; one extension batch, prespecified |

## 15. Recommended next step

The owner decides §11 items 1–2. Then a size-S issue (or a spike) builds the frame counts only, with no labels, so the strata sizes in §4.3 can be checked against what exists before the seed is published.
