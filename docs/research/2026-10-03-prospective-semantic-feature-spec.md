# Prospective Semantic Feature Specification (record contract, research-only)

**Brief:** #618  ·  **Date:** 2026-10-03  ·  **Status:** DRAFT research specification. It defines **what** every prospective semantic-feature record must hold and the rules that keep it honest. It is **not** a build spec. No code, schema, dependency or job follows from it until the governance gates in §2 pass and a normal spec and plan are approved.  ·  **Agent/model:** main session (research lead)

**Companion artifacts (#618):** [taxonomy v0](2026-10-03-semantic-taxonomy-v0.md) · [annotation protocol](2026-10-03-disclosure-annotation-protocol.md) · [evidence review](2026-10-03-disclosure-change-evidence-review.md) · [ML validation brief](2026-10-03-ml-validation-methodology-brief.md).

---

## 1. Objective

Make forward-generated semantic features into **the strongest evidence the program can produce**: outputs computed before their outcomes exist, under frozen definitions and configurations, never regenerated, with enough provenance to audit every number later. Historical tests with modern pretrained models are exploratory at best ([ML brief §7](2026-10-03-ml-validation-methodology-brief.md#7-pretrained-model-contamination-evidentiary-weights)). Prospective records are how that limit is escaped.

## 2. Scope and governance preconditions

- **In:** the record contract (§4), versioned configuration objects (§3), immutability and correction rules (§7), `known_at` (§4.3), routing and fallback logging (§5), cost logging (§6), drift monitoring (§8), and the freeze checklist that starts capture (§9).
- **Out:** storage technology, table DDL, job scheduling, provider choice. These belong to a later spec and plan, through the normal process.
- **Provider neutrality:** the contract is the same for every arm: deterministic, classical, encoder, Jev, small or frontier generative, and cascade. No field assumes a vendor.
- **Preconditions before any capture runs:**
  1. A **research-measurement-boundary ADR** is accepted. It must say: model calls happen only in a segregated research job; outputs go only to a research store; no code path reads them into the runtime store, the decision, order or fill tables, config or the risk-gated wrapper; and this is enforced by tests like ADR 0008 point 2's import and table-name scan. Today ADR 0008 point 1 forbids any LLM client or SDK, and option 4 rejects an LLM as an extractor into the store ([ML brief §2](2026-10-03-ml-validation-methodology-brief.md#governance-gates)).
  2. A research spend ceiling above $0 (ADR 0005, ADR 0008 point 7 by analogy).
  3. Taxonomy v1 is frozen for the captured dimensions × modes (protocol §8 gates passed).
  4. The inference configuration is frozen (§9).
- **Which preconditions apply to what:**

| What is captured | Needs |
|---|---|
| Model arms 5–9 | **All of 1–4** |
| Arm 1 (majority) and the [ML brief §6](2026-10-03-ml-validation-methodology-brief.md#6-deterministic-baselines-permanent-controls) deterministic change features | Only an approved corpus spec and a research store (no model, no labels) |
| Arms 2–4 | Taxonomy v1 (3), because they are trained on its labels |

- **Timing:** capture of model arms starts **as soon as all of 1–4 hold**, in parallel with the remaining historical work. It does **not** wait for economic or return results (TC §13). Waiting only shortens the prospective record.

## 3. Versioned configuration objects

Each of these is immutable once used. A change is a new version id, never an edit.

| Object | Identity | Holds |
|---|---|---|
| `taxonomy_version` | e.g. `financial_semantics_v1` + document blob hash | dimensions, labels, definitions, polarity table |
| `prompt_version` | id + sha256 of the full template, including label names **and** definitions | instruction text, label order, any few-shot examples (none from the sealed test) |
| `model_config` | id | provider, **requested** model id (pinned, never a moving alias such as "latest"), endpoint, SDK version, decoding settings (temperature and the like), structured-output schema, max tokens, tools / browsing / retrieval **disabled** (ADR 0008 point 6 by analogy) |
| `retrieval_version` | id + code version | section parser version, passage selection and alignment rules (end-to-end mode, ML brief §5.2 B) |
| `calibration_version` | id | calibrator type, its parameters, the split it was fitted on (`cal` only), and the fit date |
| `routing_policy_version` | id | routing score definition (§5), threshold(s), fallback chain, the unresolved rule |
| `feature_set_version` | id | the list of (dimension, mode, basis) features emitted, and how evidence statuses are encoded |
| `price_snapshot` | id + date + source | per-token or per-call prices used to compute cost (§6) |

## 4. The record contract

### 4.1 One inference record per (observation, system, attempt)

Every field below is required unless marked nullable. "Observation" is as defined in [taxonomy §4.1](2026-10-03-semantic-taxonomy-v0.md#41-the-observation).

| Group | Field | Notes |
|---|---|---|
| Identity | `record_id` | unique, never reused |
| | `observation_key` | (CIK, current accession, dimension, scope, mode, basis), stable across systems |
| | `security_id` | point-in-time resolution at the current filing's acceptance (event-data spec rule). Nullable with a reason (`unknown_cik`, `unlisted`) |
| | `cik` | |
| Sources | `previous_accession`, `current_accession` | `previous_accession` nullable only in `narrated` mode |
| | `previous_doc_sha256`, `current_doc_sha256` | raw archived bytes |
| | `previous_text_sha256`, `current_text_sha256` | normalized section text the offsets refer to |
| | `source_available_at` | the current filing's EDGAR acceptance time, UTC, from submissions (EP P12) |
| Task | `topic` (dimension), `scope_type`, `scope_name`, `measurement_mode`, `comparison_basis`, `current_report_period`, `comparison_period`, `target_period` (nullable) | the taxonomy fields |
| Versions | `taxonomy_version`, `prompt_version`, `model_config`, `retrieval_version`, `calibration_version`, `routing_policy_version`, `feature_set_version` | §3 |
| Model identity | `model_provider`, `model_id_requested`, `model_id_observed` (as the response reports it), `model_version` (as documented for that id) | A mismatch between requested and observed is stored, never "fixed" |
| Output | `evidence_status` | the 5 statuses; `extraction_failure` when the pipeline could not produce text |
| | `selected_label` | null unless `evidence_status = sufficient` |
| | `label_order` | the exact ordered label list given to the system |
| | `raw_probability_vector` | as returned, keyed by label. Null if the system exposes none, with `probability_source` = `none` |
| | `probability_source` | `native` (classifier or vendor probabilities) / `label_token_logprobs` / `verbalized` / `none` |
| | `calibrated_probability_vector` | from `calibration_version`; null if not calibratable |
| | `vendor_confidence` | nullable; stored **separately**, never substituted for a probability |
| | `evidence_spans` | list of (document, section, char_start, char_end, span_sha256). Every span must resolve to the archived text by hash, else `provenance_valid = false` |
| | `provenance_valid` | computed by code at write time |
| Routing | `routing_score_name`, `routing_score_value`, `routing_decision`, `fallback_record_id`, `final_outcome`, `unresolved_reason` | §5 |
| Timing | `inference_started_at`, `inference_completed_at` | UTC, tz-aware |
| | `known_at` | §4.3 |
| Usage | `input_tokens`, `output_tokens`, `cached_tokens`, `requests_made`, `retries` | per record. Shared-context batches are allocated by a stated rule |
| | `measured_cost_usd`, `price_snapshot` | §6 |
| Raw | `request_sha256`, `response_sha256`, `raw_response` | the raw response is stored in full (or content-addressed), so the record can be re-derived without re-calling the model |
| Lineage | `supersedes_record_id`, `correction_reason`, `created_at` | §7 |

### 4.2 Human-label records

Benchmark gold labels use the [protocol §7](2026-10-03-disclosure-annotation-protocol.md#7-record-and-provenance-per-label) layout and live beside, never inside, model records. A prospective sample is human-labeled **after** its outputs are frozen, so live accuracy can be measured without the labelers seeing model outputs.

### 4.3 `known_at`

`known_at = max(source_available_at, inference_completed_at)`, and for a cascade the **final** record's completion. For `routed_human`, completion is the human decision's timestamp. A feature is never earlier than the filing's acceptance, and never earlier than the moment the system actually had the value. Investability uses the first trading session strictly after `known_at` (ML brief §9.2). A record written late (after an outage, say) keeps its true late `known_at`. It is never back-stamped.

## 5. Routing and fallback

- **`routing_score_name`** is exactly one of: `calibrated_p_max`, `raw_p_max`, `margin`, `entropy`, `vendor_confidence`, or a validated composite. It is frozen in `routing_policy_version`. These are different statistics and never interchangeable ([ML brief §5.5](2026-10-03-ml-validation-methodology-brief.md#55-probabilities-and-calibration)).
- **`routing_decision`** ∈ {`accepted`, `routed_fallback`, `routed_human`, `unresolved`}, with the threshold that applied stored in the record.
- **Every fallback call is its own record** with its own fields, linked by `fallback_record_id`. The final feature value comes from the last record in the chain. **`final_outcome`** ∈ {`accepted_primary`, `accepted_fallback`, `accepted_human`, `unresolved`, `failed`}.
- An `unresolved` or `failed` observation **stays in the dataset** as a missing-value indicator, never dropped (TC §16). Abstention can bias the economic sample, which is why.
- **Model abstention is not an evidence status.** A system may judge the evidence `sufficient` and still be unresolved after routing (taxonomy §4.4).

## 6. Cost measurement

- `measured_cost_usd` = logged usage × the `price_snapshot` in force at call time. The snapshot is recorded with its source and date.
- Each month, the **billed** amount from the provider invoice is reconciled against the sum of `measured_cost_usd`, and the difference is reported. Advertised prices are never treated as observed cost (TC §21).
- **Reports:**
  - cost per 1,000 observations;
  - cost per accepted, provenance-valid feature;
  - cost per correct adverse label, on the human-labeled prospective sample;
  - the human-review hours behind `routed_human`.
- A monthly cap from config stops new calls when reached. Skipped observations are recorded as `failed` with reason `cost_cap` and are never queued and back-filled later with a false `known_at`.

## 7. Immutability and corrections

1. Records are **append-only**. No update or delete, ever, including after outcomes are known.
2. A **correction** (a bug fix in parsing, a wrong span offset, a mis-resolved CIK) is a **new record** with `supersedes_record_id` and `correction_reason`, written with its own `created_at` and its own `known_at` = correction time. The original stays.
3. **Analyses declare which view they use:**
   - the **as-captured** view (original records only) is the prospective evidence;
   - the **as-corrected** view is diagnostic.

   A correction made after outcomes are visible never enters the as-captured view.
4. A **new configuration** (prompt, model, taxonomy or calibration version) is a **new feature stream**, not a correction. Old streams keep running, or stop, but are never overwritten.
5. **Re-running an old configuration on old filings** (for example after a provider outage) produces records stamped with the re-run time. Those are historical-style, not prospective.

## 8. Drift and version monitoring

- **`model_id_observed` vs `model_id_requested`:** any mismatch raises a research alert and opens a new stream boundary in analysis.
- **Drift probe** (as ADR 0008 point 6 does for memos): on a fixed schedule, re-send a frozen panel of stored inputs and record label flips and probability shifts against the original records. Probe outputs are stored apart, never used as features, and never displayed as features.
- **A provider retiring a pinned version ends that stream.** A replacement model is a new stream with its own benchmark entry. It is never a silent substitute.
- **Stop condition** ([ML brief §13](2026-10-03-ml-validation-methodology-brief.md#13-stop-conditions-any-one-stops-narrows-or-rejects-a-branch-each-is-recorded-as-a-negative-result)): if the probe's label-flip rate at the same requested version exceeds a threshold frozen in `routing_policy_version`, the stream cannot be treated as one measurement. The threshold is set from the benchmark's test–retest floor.

## 9. Freeze checklist (capture starts only when every box is true)

- [ ] Taxonomy v1 frozen; dimensions × modes listed.
- [ ] Each captured system's `model_config`, `prompt_version`, `retrieval_version`, `calibration_version` and `routing_policy_version` frozen, with their benchmark results registered (ML brief §10).
- [ ] The sealed-test scoring for those configurations registered **before** capture starts, so the prospective record is not selected on how capture went.
- [ ] Outcome definitions and horizons for the prospective tests pre-registered: the E1 target ([ML brief §8.1](2026-10-03-ml-validation-methodology-brief.md#81-first-hypothesis-e1-demand-deterioration--next-quarter-revenue-growth-deceleration)), the return horizon, and the human-labeled live-accuracy sample size.
- [ ] The research-measurement-boundary ADR accepted, and its isolation tests passing.
- [ ] Spend ceiling set; `price_snapshot` recorded.
- [ ] The research store's append-only guarantee tested (no update/delete path).

## 10. Evidence, inference, recommendation, unknown

| Kind | Statement |
|---|---|
| **Evidence** | Modern pretrained models show measurable look-ahead on historical financial text, and date instructions do not remove it (Sarkar & Vafa 2024; Lopez-Lira, Tang & Zhu 2025). Temperature-0 API calls are not reproducible, and vendors re-route models (ADR 0008 context, from the handoff). |
| **Inference** | Only outputs fixed before outcomes exist avoid both problems. A record that cannot be re-derived from stored raw responses cannot be audited. |
| **Recommendation** | Capture the deterministic change features once a corpus spec and research store exist; arms 2–4 once taxonomy v1 exists; model arms the day the freeze checklist passes. Never regenerate. |
| **Unknown** | Prospective sample sizes. At about 1,000 issuers × 4 filings a year, one year gives about 4,000 filing pairs per dimension. Outcomes for a one-quarter fundamentals target mature about a quarter later, and return power stays low for years (QI-1). |

## 11. Unresolved questions

1. The research store's location and technology (shared with the research registry, ML brief §10).
2. Whether shared-context batching (several dimensions per call) is allowed in v1. It cuts cost but couples dimensions' errors. Cost allocation per record must then be by a stated rule.
3. The human-labeled prospective sample: its size and cadence, for measuring live accuracy and drift.
4. Retention of raw responses that may contain provider-licensed text: check provider terms before storage.

## 12. Proposed tests (for the eventual build spec)

- A write rejects any record missing a required field or with an unresolvable span hash.
- No update or delete path exists on the research store's record tables (a test attempts both and expects refusal).
- `known_at` ≥ `source_available_at` and ≥ `inference_completed_at`, always.
- An import and table-name scan shows that no runtime module reads research records (ADR 0008 point 2 pattern).
- Replaying a stored `raw_response` reproduces every derived field exactly.
- A requested/observed model-id mismatch produces an alert and a stream boundary.

## 13. Acceptance criteria for this spec

- The owner accepts the field list and the §7 rules as the contract any future capture spec must satisfy.
- A future build spec cites this document and maps every §4 field to storage.

## 14. Dependencies

The document corpus and section parser (taxonomy §12); taxonomy v1; the research-registry spec; the research-measurement-boundary ADR; a spend ceiling; the event-data spec's CIK→security resolution.

## 15. Risks

| Risk | Mitigation |
|---|---|
| Silent vendor re-route | `model_id_observed`; the drift probe; a stream boundary |
| Quiet regeneration after outcomes | Append-only store; the as-captured view; correction lineage |
| Costs drift above the cap | Cap from config; skipped records counted |
| Abstentions bias the sample | Unresolved and failed records retained as indicators |
| Capture delayed waiting for "complete" research | §2 timing rule: capture starts at the freeze, not after the return tests |

## 16. Recommended next step

Include this contract's §2 preconditions and §7 rules in the research-measurement-boundary ADR draft. The owner may also want prospective capture of the deterministic features in scope for the future document-corpus spec. That is a scope decision for that spec, raised here as a question, not added by this document.
