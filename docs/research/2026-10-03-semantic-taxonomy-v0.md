# Financial Semantic Taxonomy v0: demand, inventory pressure, liquidity pressure

**Brief:** #618  ·  **Date:** 2026-10-03  ·  **Status:** DRAFT v0 for the annotation pilot. Not frozen. It becomes `financial_semantics_v1` only through the acceptance criteria below, as a new document.  ·  **Agent/model:** main session (research lead)

**Program order:** Define → Label → Measure → Validate semantics → Validate economics → Validate prediction → Validate portfolio use. This document is the **Define** step. Nothing here measures anything, and nothing here grants a model any role. [ADR 0008](../decisions/0008-llm-role.md) option 4 (an LLM as an extractor into the store) and charter principle 3 stay in force. See the [ML validation brief](2026-10-03-ml-validation-methodology-brief.md#governance-gates) for the gates.

**Companion artifacts (#618):** [annotation protocol](2026-10-03-disclosure-annotation-protocol.md) · [evidence review](2026-10-03-disclosure-change-evidence-review.md) · [ML validation brief](2026-10-03-ml-validation-methodology-brief.md) · [prospective feature spec](2026-10-03-prospective-semantic-feature-spec.md).

**Inputs:** the 2026-10-03 notes on branch `spike/ml-llm-research-notes` (commit `587cf8a`, not merged to main): the typed-classifiers research results (cited below as **TC**, §10–11), the neglected-equities note (**NE**, §3–4), the integration note (**INT**) and the claims pilot (**CP**).

---

## 1. Objective

Define three economic concepts precisely enough that two trained people, reading the same pair of disclosures, independently give the same label most of the time, for reasons they can point to in the text. Whether they actually do is the pilot's question, not this document's.

## 2. Scope

- **In:** three dimensions only: **D1 demand**, **D2 inventory pressure**, **D3 liquidity pressure**. Two label fields per observation: evidence status and direction. Two measurement modes, four comparison bases, one record layout.
- **Out (deferred, §9):** pricing power, backlog quality, customer concentration, management confidence, quantitative specificity, risk escalation, and the cross-document relation task (that task gets its own label set in the [annotation protocol §9](2026-10-03-disclosure-annotation-protocol.md#9-separate-task-cross-document-relation-labels)). **The taxonomy is not expanded until D1–D3 pass the pilot's agreement gate (§11).**
- **Documents in v0:** 10-Q and 10-K MD&A (Item 2 of a 10-Q, Item 7 of a 10-K), plus the 10-K Liquidity and Capital Resources subsection, which sits inside MD&A. Risk Factors, earnings releases (8-K Item 2.02 exhibits) and call transcripts are out of v0. TC §7 cites published evidence of domain shift between SEC filings and earnings-call language, so each document type needs its own pilot, never a pooled one.
- **Issuers:** inside the [ADR 0006](../decisions/0006-universe-and-cadence.md) universe, per the owner's 2026-10-03 decision on CP tension X1. **Financial-sector issuers** (banks, insurers, broker-dealers, REITs; SIC 6000–6799) are **out of v0 for all three dimensions**: "demand", "inventory" and "liquidity" mean different things for them, and liquidity is a regulatory construct there.

## 3. Evidence, inference, recommendation, unknown

| Kind | Statement |
|---|---|
| **Evidence** (published, as reported in TC §7 and §11, **pending the source check in the [evidence review](2026-10-03-disclosure-change-evidence-review.md)**) | Changes between a firm's periodic filings relate to later operations and returns (Cohen, Malloy & Nguyen, *Lazy Prices*, JF 2020). Textual measures in finance are imprecise and sensitive to implementation (Loughran & McDonald, JAR 2016). LLM reasoning gets worse as tasks move from single-document to cross-period and cross-entity work, with time and entity mismatches among the errors (Fin-RATE, arXiv 2602.07294). |
| **Inference** | "Sentiment" is not one quantity. *"Demand remained resilient despite moderation in selected end markets"* can support several readings (TC §9 of the original brief). A label is only reproducible if it names the topic, the scope, the comparison period and the comparison basis. |
| **Inference** | A forced direction on a topic the text does not discuss makes up a measurement. Absence must be its own state, not "unchanged". |
| **Recommendation** | Split every label into an **evidence status** and a **direction**. Assign a direction only when the status is `sufficient`. |
| **Unknown** | Whether humans can reach acceptable agreement on any of D1–D3. Nothing in the repo or the inputs measures this for paired financial disclosures. |

## 4. Core concepts

### 4.1 The observation

One observation is one **(issuer, dimension, scope, comparison basis, measurement mode, document pair)** with one evidence status and, if sufficient, one direction. One document pair yields **at least three observations** (one per dimension, total-company scope). It may yield more where the text discusses a named segment or geography.

Nine labels on one pair are **not** nine independent data points. Agreement and model statistics cluster by pair and by issuer ([ML brief §3](2026-10-03-ml-validation-methodology-brief.md#3-splits-leakage-and-dependence)).

### 4.2 Measurement modes (never pooled)

| Mode | What is measured | Spans used |
|---|---|---|
| `cross_document` | How the company's characterization of the dimension **changed between two documents** ("strong across all segments" at t−1, "healthy among strategic accounts" at t). | Previous span **and** current span, both required. |
| `narrated` | The direction the **current document itself states** against its own comparator ("demand declined compared with the prior-year quarter"). | Current span required. Previous span recorded if one was consulted, else null. |

These are different quantities. A company can narrate "lower demand year over year" in two consecutive 10-Qs: `narrated` gives *deteriorated* both times, while `cross_document` gives *unchanged* at t. The pilot labels both on every pair, and agreement is reported per mode. **Primary mode for v1: chosen by the pilot (§11)**, never by later economic or return results.

### 4.3 Comparison basis (never silently mixed)

| `comparison_basis` | Meaning | Typical pair or source |
|---|---|---|
| `sequential` | Period t against the immediately preceding period t−1. | 10-Q Q2 vs 10-Q Q1; 10-Q Q3 vs 10-Q Q2. |
| `year_over_year` | Period t against the same fiscal period a year earlier. | 10-Q Q1 FY26 vs 10-Q Q1 FY25; 10-K vs prior 10-K. |
| `guidance_revision` | A company's **formal outlook** (stated ranges or numbers, or explicitly labeled guidance) for a fixed future period P, as stated at t−1 and as restated at t. | Outlook paragraphs in MD&A, later in releases. |
| `fixed_future_period` | **Qualitative** expectations about the same fixed future period P, stated at t−1 and at t, that are not formal guidance ("we expect demand in the second half to …"). | MD&A outlook language. |

Rules:
- `sequential` and `year_over_year` describe **realized** periods. The two forward bases describe **expectations** about a period not yet realized. A forward observation records `target_period` (the fixed future period P) as well as both report periods.
- **Form mismatch.** A Q1 10-Q's sequential predecessor is a 10-K, which narrates a full year. In v0 that pair is `incomparable` for `cross_document` unless both texts address the same scope and horizon. The pilot samples Q1-vs-10-K pairs as a separate stratum so the cost of this rule is measured, not assumed (protocol §4).
- **Narrated basis.** SEC Regulation S-K Item 303(c) generally has a 10-Q compare the current quarter with the same quarter of the prior year. The 2020 amendments also allow a sequential comparison. Annotators record the basis **the text uses**, not the one they expect. *(The Item 303(c) reading is a regulatory fact to be confirmed against the eCFR text before v1; recorded under Unknowns.)*

### 4.4 Evidence status (one of five, always assigned)

| `evidence_status` | Assign when | Direction field |
|---|---|---|
| `sufficient` | The text addresses the dimension, for the observation's scope, basis and mode, clearly enough to assign one direction. In `cross_document`, **both** documents address it. | Required |
| `not_discussed` | The dimension is not addressed: in either document (`absent_both`), only the previous one (`absent_current`), or only the current one (`absent_previous`), recorded in `status_reason`. A topic appearing or disappearing is a disclosure-change fact. It is kept as data, never turned into a direction. | Null |
| `mixed` | The text addresses the dimension for this scope but gives evidence in conflicting directions and states no net direction (one segment up, another down, no total). | Null |
| `incomparable` | Both documents address it, but scope, period, basis or definition differ so that a direction cannot be read. Examples: total company vs one segment; sequential vs year-over-year; a 10-K annual narrative vs a quarter; an acquisition the text says changes the comparison; a changed fiscal calendar. The reason goes in `status_reason`. | Null |
| `extraction_failure` | The text needed to judge could not be obtained or used: missing or garbled section, wrong document, parse failure, truncated file. An **operational** status, not a semantic judgment. Counted, and excluded from semantic agreement. | Null |

**Annotator uncertainty is not a status.** An annotator who reads the text as sufficient but hesitates records the direction plus `annotator_confidence` (low, medium or high) and a note. A model's abstention is a third thing, the **routing** outcome `unresolved`, recorded in the [prospective spec](2026-10-03-prospective-semantic-feature-spec.md#5-routing-and-fallback), never as an evidence status.

### 4.5 Direction and polarity

| Dimension | Native direction labels (`direction`) | Adverse native label | Favorable/adverse mapping (`polarity`, derived in code, never annotated) |
|---|---|---|---|
| D1 demand | `improved`, `unchanged`, `deteriorated` | `deteriorated` | improved → favorable; deteriorated → adverse |
| D2 inventory pressure | `increased`, `unchanged`, `decreased` | `increased` | increased → adverse; decreased → favorable |
| D3 liquidity pressure | `increased`, `unchanged`, `decreased` | `increased` | increased → adverse; decreased → favorable |

**Label names are part of the measurement.** A September 2026 preprint found a typed decision head followed the option *name* rather than the rubric bound to it (arXiv 2609.26758; verified via abstract only; see the [ML brief §5.8](2026-10-03-ml-validation-methodology-brief.md#58-robustness-negative-controls-error-taxonomy)). So label names here are plain words that carry their own meaning. Whenever a model is given these labels, the definitions travel with the names, and a name–definition swap test is mandatory.

The native label is stored. The polarity is derived from a versioned table in code. "Adverse-state precision and recall" in every later artifact means the adverse native label in this table. **`unchanged` requires positive evidence of stability** ("demand remained stable"; the same substantive characterization in both documents). Silence is `not_discussed`, never `unchanged`.

### 4.6 Rules shared by all three dimensions

1. **The text, not the world.** Labels describe what the supplied disclosures say, read on their own terms. Annotators do not use later outcomes, prices, news or their own knowledge of what happened next (protocol §6). These are operational reference labels, not truth about the business (TC §11).
2. **Narrative is kept apart from fundamentals.** A number the disclosure itself states (orders, bookings, unit volume, days of inventory, revolver availability) may be evidence when **the text ties it to the dimension**. Externally calculated growth, ratios or XBRL values are never used. Those belong to the fundamentals layer, so the later "narrative vs fundamentals" tests ([ML brief §8](2026-10-03-ml-validation-methodology-brief.md#8-economic-validation-stream-stage-5)) are not circular.
3. **Attribution is required.** A revenue or volume change is demand evidence only when the text attributes it to demand (customer demand, orders, end-market activity). "Sales fell 8% on lower volumes due to supply constraints" is **not** demand evidence.
4. **Removal is not the opposite claim.** If "demand remained strong" disappears at t, that is `not_discussed / absent_current`, not *deteriorated*.
5. **Boilerplate.** Generic, unchanged risk-style language ("demand for our products may fluctuate") is not a characterization and does not support `unchanged`. A substantive statement repeated verbatim does.
6. **Hedges and qualifiers count.** Narrowing the claim's scope ("across all channels" → "among strategic accounts") or adding a qualifier that concedes weakness ("despite softer activity elsewhere") is directional evidence for `cross_document`. An unqualified intensifier change alone ("strong" → "robust") is not, unless the manual's word list says otherwise (protocol §5, item 4).
7. **Scope.** The default scope is `total_company`. A segment or geography observation needs a named reportable segment or geography in both spans. A total-company label built from segments needs a company-level statement, or segment statements covering every reportable segment the text names. Partial coverage gives `incomparable` at company level, plus segment observations.
8. **Exact spans.** Every non-`not_discussed` status needs character offsets into the archived source text for each span relied on (protocol §7).
9. **Each dimension is labeled on its own.** One sentence may be evidence for two dimensions ("customers are reducing inventory, which lowered orders": D1 deteriorated, and D2 increased at channel scope). Labeling one dimension never forces another.

## 5. D1 Demand

**Meaning.** The direction of **explicitly described demand conditions** for the issuer's products or services: customer orders, bookings, end-market or customer activity, unit volume **attributed by the text to demand**, traffic, utilization **attributed to demand**, subscriber or customer additions **described as demand**. Matched by entity, scope, basis and mode.

**Direction:** `improved` / `unchanged` / `deteriorated`.

**Comparison basis:** any of the four. `guidance_revision` applies only where the outlook is stated in demand terms ("we now expect lower order volumes in Q4"), not as a revenue range alone.

**Applicable scope:** all non-financial issuers in the v0 universe. Recorded per scope (total company, segment, geography).

**Exclusions:**
- Revenue, price or mix changes not attributed to demand (rule 3).
- Supply-constrained volume ("we could have sold more").
- Pricing actions (deferred: pricing realization and pricing power).
- Backlog levels (deferred: backlog quality). Bookings and orders count.
- Management tone about the business overall ("we are confident in our strategy").
- Macro commentary not linked to the issuer's own demand ("the economy slowed").

**Illustrative examples.** These are constructed for this document, **not quotations from filings**. The pilot replaces them with real spans (protocol §5).

| Kind | Previous span (t−1) | Current span (t) | Label (`cross_document`, `sequential`, total company) |
|---|---|---|---|
| Positive (adverse) | "Demand remained strong across all customer segments." | "Demand remained healthy among our largest strategic accounts, despite softer activity elsewhere." | `sufficient`, `deteriorated` (scope narrowed, weakness conceded) |
| Positive (favorable) | "Order intake was below the prior quarter as customers delayed projects." | "Order intake recovered as delayed projects were released, and bookings exceeded the prior quarter." | `sufficient`, `improved` |
| Negative (stable) | "Customer demand was stable across our end markets." | "Customer demand remained stable across our end markets." | `sufficient`, `unchanged` |
| Negative (not demand) | "Net sales increased 4%." | "Net sales decreased 6%, primarily due to supplier shortages that limited shipments." | `not_discussed` (`absent_both`): no demand characterization; the decline is attributed to supply (rule 3) |
| Ambiguous | "Demand was strong." | "Demand remained resilient despite moderation in selected end markets." | Pilot question. Candidate rule: `deteriorated` (a concession of weakness), but annotators may read *resilient* as *unchanged*. The protocol logs this as a known hard case. |
| Counterexample (looks adverse, is not) | "Demand in our Americas segment was strong." | "Demand in our Americas segment was strong; we exited the low-margin EMEA distribution business." | Total company: `incomparable` (scope changed by a divestiture); Americas segment: `unchanged`. Exiting a business is not weaker demand. |
| Mixed | "Demand was steady." | "Demand improved in Industrial and weakened in Consumer." (no total) | Total company: `mixed`; segment observations: Industrial `improved`, Consumer `deteriorated` |

## 6. D2 Inventory pressure

**Meaning.** The direction of **explicitly described excess-inventory pressure**: elevated or excess inventory relative to demand, obsolescence, write-downs, reserves for excess or obsolete stock, markdowns or clearance to reduce inventory, production cuts to bring inventory down, and **channel or customer destocking** where the text describes it. Recorded with `inventory_locus` ∈ {`own`, `channel`}.

**Direction:** `increased` / `unchanged` / `decreased` (native), with `increased` adverse. **Lower inventory is not automatically lower pressure.** A falling balance attributed to strong sell-through is evidence of decreased pressure. A falling balance caused by write-downs is evidence of increased pressure. A falling balance with no stated cause is not evidence (rule 2).

**Comparison basis:** any of the four. Narrated and cross-document modes both apply.

**Applicable scope:** non-financial issuers for which inventory is material. Applicability is **set in code, point in time**, before labeling: inventory as a share of total assets in the **previous** filing's XBRL facts (as filed, `known_at` ≤ the previous filing's acceptance), at or above a config threshold (a candidate value is fixed in the protocol before sampling, not here). Annotators see the flag. When it is false the observation is `not_applicable` and is never labeled. That is a sampling exclusion, not a sixth evidence status.

**Exclusions (deliberate, v0):**
- **Shortage, supply constraint and under-stocking.** This is a different economic state (often strong demand plus constrained supply), so it is not the negative end of excess pressure. Folding it in would make "increased pressure" mean two opposite things. Logged as candidate dimension *supply constraint* (§9).
- Inventory build described as **strategic** (pre-buying ahead of a launch or tariff), unless the text also describes it as excess.
- LIFO/FIFO, costing-method and accounting-policy changes.
- Inventory valuation numbers without narrative attribution.

**Illustrative examples** (constructed, not quotations):

| Kind | Previous span | Current span | Label |
|---|---|---|---|
| Positive (adverse) | "Inventory levels were in line with demand." | "We ended the quarter with elevated inventory and recorded a $14 million charge for excess and obsolete products." | `sufficient`, `increased`, `own` |
| Positive (favorable) | "We are working through excess inventory and expect elevated markdowns." | "Inventory has returned to normalized levels and clearance activity has ended." | `sufficient`, `decreased`, `own` |
| Channel | "Distributor inventory was at normal levels." | "Distributors continued to reduce their inventory, which weighed on our shipments." | `sufficient`, `increased`, `channel` (D1 is labeled separately and may be `deteriorated`) |
| Negative (shortage) | "Component availability limited production." | "Shortages of key components worsened and we could not meet customer orders." | `not_discussed` for D2 (shortage is excluded); a supply-constraint observation is out of v0 |
| Ambiguous | "Inventory increased to support the new product launch." | "Inventory remains elevated as the launch ramps more slowly than planned." | Pilot question: strategic build at t−1 becoming excess at t? Candidate: `increased` (slower-than-planned sell-through concedes excess). Logged as a hard case. |
| Counterexample | "Inventory was $820 million." | "Inventory decreased to $640 million." | `not_discussed`: numbers with no narrative attribution are fundamentals-layer data (rule 2) |

## 7. D3 Liquidity pressure

**Meaning.** The direction of **explicitly described funding or payment constraints**: doubt about meeting obligations; going-concern language; covenant breaches, waivers, or amendments that relieve covenants or tighten terms; stated difficulty or inability to access capital; reliance on asset sales, equity raises or drawn credit lines to fund operations; refinancing risk on stated maturities; narrowing of the company's own **liquidity-adequacy statement** (for example "sufficient for at least the next 12 months" becoming "sufficient into the third quarter of next year"); a dividend or buyback suspended **to preserve liquidity**; supplier-payment stretching described as a constraint.

**Direction:** `increased` / `unchanged` / `decreased` (native), with `increased` adverse.

**Comparison basis:** sequential and year-over-year mainly. `fixed_future_period` applies to adequacy statements about the same horizon.

**Applicable scope:** non-financial issuers in the v0 universe, total-company scope only in v0. Segments do not carry liquidity.

**Exclusions:**
- **Ratio calculation.** Current ratio, leverage, cash burn and interest coverage are computed by code from XBRL, never annotated (TC §10: "leave ratio calculation in code").
- A routine shelf registration, routine revolver renewal on unchanged terms, or opportunistic refinancing described as such.
- Generic capital-markets risk factors.
- Credit-rating actions reported without the company describing an effect on its funding.

**Illustrative examples** (constructed, not quotations):

| Kind | Previous span | Current span | Label |
|---|---|---|---|
| Positive (adverse) | "We believe existing cash and credit availability are sufficient to fund operations for at least the next 12 months." | "We obtained a waiver of our leverage covenant and are evaluating options to address our 2027 maturities; there is substantial doubt about our ability to continue as a going concern." | `sufficient`, `increased` |
| Positive (favorable) | "We are in discussions with lenders regarding covenant relief." | "We completed a refinancing that extends our maturities to 2031 and restored full availability under our revolver." | `sufficient`, `decreased` |
| Negative (stable) | Adequacy statement, 12-month horizon. | Same adequacy statement, 12-month horizon, no new constraint. | `sufficient`, `unchanged` |
| Counterexample (looks adverse) | "We have no borrowings under our revolver." | "We drew $200 million under our revolver to fund the acquisition of X." | `unchanged` if an adequacy statement is repeated; else `not_discussed`. A draw for a stated strategic purpose is not described as a constraint. |
| Ambiguous | "Liquidity remains strong." | "We drew down our revolver as a precautionary measure given market uncertainty." | Pilot question. Candidate rule: `increased` only if the text links the draw to funding concern; "precautionary" is a known hard case (common in early 2020 filings). |

## 8. Record layout (every labeled observation)

The fields below are the minimum. The annotation protocol adds annotation-process fields (§7 there), and the prospective spec adds model fields. One record = one observation.

| Field | Meaning |
|---|---|
| `observation_id` | Stable id. |
| `taxonomy_version` | `financial_semantics_v0` (this document's git blob hash recorded with it). |
| `issuer_cik`, `security_id` | Issuer CIK, and the point-in-time security mapping as resolved at the current filing's `known_at` (the event-data spec's resolution rule). |
| `dimension` | `demand`, `inventory_pressure`, `liquidity_pressure`. |
| `scope_type`, `scope_name` | `total_company` / `segment` / `geography`, plus the name as written. |
| `inventory_locus` | D2 only: `own` / `channel`. |
| `measurement_mode` | `cross_document` / `narrated`. |
| `comparison_basis` | `sequential` / `year_over_year` / `guidance_revision` / `fixed_future_period`. |
| `current_report_period`, `comparison_period`, `target_period` | Fiscal period ends. `target_period` only for the forward bases. |
| `previous_accession`, `current_accession`, `previous_form`, `current_form` | Source filings. |
| `previous_doc_sha256`, `current_doc_sha256` | Hashes of the archived raw documents. |
| `previous_spans`, `current_spans` | Lists of (section, char_start, char_end, text_sha256) into the archived parsed text. |
| `evidence_status`, `status_reason` | §4.4. |
| `direction` | Null unless `sufficient`. |
| `annotator_decision` | The annotator's own label pair, before adjudication. |
| `annotation_provenance` | Annotator pseudonym, manual version and hash, timestamp, round (independent / third / adjudication), time spent. |
| `applicability` | D2's inventory-materiality flag and inputs. |

## 9. Deferred dimensions and why

| Dimension (from the original brief) | Why deferred | Candidate re-entry test |
|---|---|---|
| Pricing power | Mixes pricing realization, pricing power and input-cost pass-through (TC §10). | Split into three, then pilot each. |
| Backlog quality | Applies to few sectors, and "quality" is undefined (TC §10). | Define sector applicability and quality criteria first. |
| Customer concentration risk | Mostly quantitative (named customers' share of revenue); better computed than annotated. | Deterministic extraction plus a narrow narrative label. |
| Management confidence; quantitative specificity | Communication variables, not economic state (TC §10). Keep apart from D1–D3. | A separate "communication" family, after the economic dimensions pass. |
| Risk escalation | Overlaps the Risk Factors deterministic baselines, so test those first. | Only if semantic labels add beyond those baselines. |
| Supply constraint (new, split out of D2) | Excluded from D2 on purpose (§6). | Pilot as its own dimension in v2 if D2 passes. |

## 10. Unresolved questions

1. **Primary mode** (`cross_document` vs `narrated`) and **primary pair type** (sequential vs year-over-year): left to the pilot's gates (§11). **Tie rule, fixed now:** if both modes pass, `cross_document` is primary, since disclosure change is what the program studies, and `narrated` is secondary. If only one passes, that one is primary. A pair type is primary only if it passes on its own stratum.
2. **Q1 vs 10-K pairs:** whether to treat them as `incomparable` by rule (losing about a quarter of pairs) or to allow the annual narrative where the text is explicitly quarterly.
3. **Intensifier word list** (rule 6): whether a bare "strong" → "solid" shift is evidence. Decided by the pilot's ambiguous-case log.
4. **D2 materiality threshold:** its value, fixed before sampling.
5. **Regulatory fact to confirm:** the exact Item 303(c) wording on quarterly comparisons, after the 2020 amendments.
6. **Earnings release vs 10-Q timing:** releases usually precede the 10-Q, so a 10-Q label may repeat information already public (TC §20). This is irrelevant to labeling. It matters for economic tests and is handled in the ML brief.

## 11. Proposed tests and acceptance criteria (for v0 → v1)

**Test:** the annotation pilot in the [protocol](2026-10-03-disclosure-annotation-protocol.md), on about 200 pairs.

**Acceptance per dimension × mode:** gates G1–G5 and the decision table in [protocol §8](2026-10-03-disclosure-annotation-protocol.md#8-agreement-analysis-plan-prespecified) are the only authority. They are fixed before labeling begins, and none of their numbers is restated here. In outline: α on the joint label, agreement on sufficiency, and adverse-class specific agreement, all of which must pass whatever the class prevalence.
- `sufficient` vs not-`sufficient` agreement α ≥ 0.667: humans must agree on *whether* there is evidence before *which* direction counts.
- Span overlap between annotators, for agreed `sufficient` labels, reported (no gate in v0).

**Outcomes:**
- **Pass:** freeze as `financial_semantics_v1`, a new document with this one superseded.
- **Revise:** at most two revision rounds. Each revision is tested on **fresh pairs**, never on the pairs that motivated it. **Only the final fresh-pair round decides.** Earlier rounds are development.
- **Fail after two rounds:** drop the dimension, or narrow it (one mode, one basis, or one sector group). A narrowing must be **written down before** a further fresh-pair round and is judged only on that round. Picking whichever subgroup happened to pass is not allowed. Every round is logged (protocol §10) and the negative result recorded.

## 12. Dependencies

- **Pilot corpus:** 10-Q and 10-K bodies with MD&A sections, archived with hashes. **The store holds none today** (CP §4; `src/tradepartner/store/schema.py` has no document or section table). See protocol §4.
- **D2 applicability:** XBRL inventory and total-asset facts as filed. Whether these are in the store needs checking (CP §4, "Partly?").
- **Owner decisions:** who annotates and the annotation budget (protocol §3).

## 13. Risks

| Risk | Effect | Mitigation |
|---|---|---|
| "Unchanged" dominates | High aggregate agreement that hides failure on the adverse class | Class-specific gates; adverse enrichment in a separate challenge sample |
| Definitions tuned to the pilot pairs | Agreement overstated | Revisions are tested on fresh pairs only |
| Annotators know what happened later (COVID, 2022 inventory glut) | Hindsight leaks into labels | Mixed periods; an explicit instruction; a hindsight check (protocol §6) |
| Boilerplate-heavy MD&A | Few `sufficient` pairs | Measure the `not_discussed` rate in the pilot; widen the document set only after |
| Taxonomy creep | Dimensions added before D1–D3 pass | Expansion is blocked by §2 |

## 14. Recommended next step

Owner review of §4.2 (two modes), §4.3 (form-mismatch rule) and the D2 shortage exclusion, then the annotation protocol's owner decisions (annotators, budget, corpus). No labeling starts until the protocol's manual is frozen.
