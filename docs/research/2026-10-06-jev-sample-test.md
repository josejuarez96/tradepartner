# Research Report: Jev sample test of the research-labeling pilot (run 1, run 2, blind gold review, spec audit)

**Brief:** #946 (the sample test), committed under #1016  ·  **Date:** 2026-10-06 (runs 2026-10-05/06; committed 2026-10-10)  ·  **Status:** COMPLETE  ·  **Agent/model:** spike and audit by a Claude Code agent (read-only, outside the repo); vendor model `jev-1.13.0`; committed by team evidfu

This report puts the read-only Jev sample test into the repo so that the evidence lines **E1 to E14** of the [research-labeling spec's amendment of 2026-10-06](../specs/research-labeling.md) (#1015) and the [research-labeling plan](../plans/research-labeling.md) resolve here. Where the amendment cites a section label (A1 to A12), it means the section of the **run 2** summary below unless it says run 1.

## Answer
**Verdict:** SUPPORTED for the amendment's streamlined pilot (one notice plus at most one 8-K, a minimal drift probe, one spend ceiling); INSUFFICIENT for model accuracy beyond a rough check (16 agent-labelled cases)  ·  **Confidence:** medium

About **11,150** listing ends 2016-01 to 2026-09, not the spec body's ~5,000. The whole population costs under $1 at list price; the two 308-call runs cost $0.036 together. The Form 25's own EX-99.25 notice resolves 82% of listing ends on its own (every NYSE-family notice states the reason; Nasdaq's is often a stub), and the nearest 8-K lifts coverage to **95%**. Answers were identical on repeat (20/20) and under a reversed option order (20/20 in run 2). Probabilities sit at 1.0 (median), so calibration is near-degenerate at this n. On 16 blind agent-labelled cases the final answer was right on 15, with 0 wrong and 1 `unresolved`.

## Method
- **Script:** `jev_sample.py` (offline harness `selftest_jev_sample.py`) in `~/tradepartner-probes/jev-spike/` on the owner's machine, outside the repo. It drew 8 quarterly EDGAR `form.idx` files (2016Q2 to 2025Q2), fetched 60 Form 25 originals per quarter and stratified 150 listing ends by `rule_provision`, seed 20261005. It touched neither the owner's store nor the repo.
- **Calls:** 308 per run: passage A on all 150, passage B (A plus the nearest 8-K in [-45, +20] days) on the 98 with one, 20 items repeated twice and once with the option order reversed. The model id was pinned to `jev-1.13.0`, with the owner's key exported for the one command and a $5 hard cap.
- **Run 1** (`out-run1/`): passage A = the Form 25 XML fields only. **Run 2** (`out/`): passage A = the fields plus the EX-99.25 notice exhibit when it is text. The gold review of run 1 showed the notice text was missing from passage A, which is why run 2 was made.
- **Gold review:** a Claude Code agent labelled 16 listing ends blind (labels written before the model columns were opened), with two labels each: `agent_label` (the true reason, wider search allowed) and `text_states` (what the linked filings state).

**Not committed here:** `calls.jsonl` (1.7 MB of raw vendor requests and responses; the owner keeps it, and seven of its responses become test fixtures by an owner copy under C9 of the amendment), `passages.jsonl`, `results.csv`, `sample.csv`, `planned_calls.csv`, `population.csv`, `gold_candidates.csv`, the script and its EDGAR cache. Everything below is aggregate statistics computed by code, agent-written notes, and short quotes from public SEC filings. Nothing here holds a key. The vendor assigns output to the customer and places no restriction on publishing results ([paid-vendor terms](2026-10-06-paid-vendor-terms.md), rows 4 and 5).

## Caveats & gaps
- One vendor model version (`jev-1.13.0`); cross-version drift could not be tested.
- The gold set is 16 cases labelled by an agent, not the owner, and untimed: a rough check, not a gate. The owner's time per case (audit A13) was never measured.
- Token estimates at 4 characters per token run 1.5 to 1.7x low (E9).
- The spike's 8-K item list differs from owner decision 6's (E14); the 95% coverage was measured without item 5.03.
- The rule answers from the owner's store were not used (audit A12), so the shortlist size is unmeasured.

## Run 1: passage A = Form 25 XML fields only (`out-run1/summary.md`)

Copied as generated, headings demoted. Section A9's `gold_candidates.csv` is not committed (it is the hand-label template; the filled result is the gold review below).

Generated 2026-10-06T03:26:25+00:00; model requested `jev-1.13.0`; price snapshot $0.042/M input tokens; seed 20261005; quarters 2016Q2,2017Q4,2019Q1,2020Q3,2021Q2,2022Q4,2024Q1,2025Q2.

### A1. Population size and cost of the whole population

| quarter | index rows | originals | 25 | 25-NSE | amendments |
|---|---|---|---|---|---|
| 2016Q2 | 353 | 191 | 33 | 158 | 4 |
| 2017Q4 | 488 | 261 | 39 | 222 | 5 |
| 2019Q1 | 442 | 219 | 16 | 203 | 16 |
| 2020Q3 | 387 | 206 | 28 | 178 | 2 |
| 2021Q2 | 541 | 280 | 19 | 261 | 0 |
| 2022Q4 | 823 | 431 | 41 | 390 | 2 |
| 2024Q1 | 464 | 248 | 36 | 212 | 4 |
| 2025Q2 | 454 | 239 | 29 | 210 | 4 |

Mean 259 originals per sampled quarter, so about **11,153 listing ends 2016-01 to 2026-09** (spec assumed 'a few thousand', ~5,000). Mean estimated tokens per call: passage A 450, passage B 1,257. Whole population at list price: A only **$0.21**, B for every listing end **$0.59**, spec worst case (8,400 tokens each) $3.93.

rule_provision among fetched candidates: `12d2-2(a)(3)` 205, `12d2-2(a)(1)` 76, `12d2-2(b)` 72, `12d2-2(a)(2)` 68, `pre_xml` 56, `12d2-2(a)(4)` 3

### A2. Is 8-K context available?

- Sampled listing ends: 150; with an 8-K in [-45, +20] days and text extracted: 98/150 (65%).
- Context notes: ok: 86, no 8-K in window: 52, no relevant item segmented; body head used: 12
- By provision (n, with 8-K): `12d2-2(a)(1)` 37/27; `12d2-2(a)(2)` 37/17; `12d2-2(a)(3)` 37/24; `12d2-2(a)(4)` 3/3; `12d2-2(b)` 36/27

Planned calls: 308 (A-base 150, B-base 98, B-repeat1 20, B-repeat2 20, B-reversed 20); estimated 269,582 input tokens, **$0.0113** (cap $5.0). EDGAR requests this run 0, cache hits 751.

### A3. Answer distribution and `unresolved`

- Passage A (n=150): unresolved 150/150 (100%); p(choice) < 0.6 0/150 (0%); p(choice) >= 0.999 55/150 (37%); median p(choice) 0.990.
  - `unresolved` 150
- Passage B (n=98): unresolved 37/98 (38%); p(choice) < 0.6 8/98 (8%); p(choice) >= 0.999 25/98 (26%); median p(choice) 0.980.
  - `unresolved` 37, `merger_or_acquisition` 21, `instrument_retirement` 21, `bankruptcy` 11, `compliance_delisting` 5, `redomicile_or_reorganisation` 2, `going_private` 1

### A4. Does the 8-K change the answer? (A vs B on the same listing end)

- Same option 37/98 (38%); same class 37/98 (38%).
- A `unresolved` -> B resolved: 61; A resolved -> B `unresolved`: 0; resolved-to-different-option flips: 0.
- Changes A -> B: `unresolved` -> `merger_or_acquisition` 21; `unresolved` -> `instrument_retirement` 21; `unresolved` -> `bankruptcy` 11; `unresolved` -> `compliance_delisting` 5; `unresolved` -> `redomicile_or_reorganisation` 2; `unresolved` -> `going_private` 1

### A5. The deterministic `rule_provision` arm

Share of model answers inside the arm's option set (the arm reads one Form 25 field; if the model mostly lands inside it, passage A adds little beyond code).
- Passage A: inside arm 0/150 (0%); unresolved 150.
- Passage B: inside arm 54/98 (55%); unresolved 37.

| provision | n | passage A answers | passage B answers |
|---|---|---|---|
| `12d2-2(a)(1)` | 37 | unresolved 37 | unresolved 14, instrument_retirement 12, compliance_delisting 1 |
| `12d2-2(a)(2)` | 37 | unresolved 37 | unresolved 10, instrument_retirement 6, merger_or_acquisition 1 |
| `12d2-2(a)(3)` | 37 | unresolved 37 | merger_or_acquisition 18, unresolved 3, instrument_retirement 1, redomicile_or_reorganisation 1, going_private 1 |
| `12d2-2(a)(4)` | 3 | unresolved 3 | unresolved 1, instrument_retirement 1, merger_or_acquisition 1 |
| `12d2-2(b)` | 36 | unresolved 36 | bankruptcy 11, unresolved 9, compliance_delisting 4, merger_or_acquisition 1, instrument_retirement 1, redomicile_or_reorganisation 1 |

### A6. Stability (same request repeated) and option order

- Identical choice across base + 2 repeats: 20/20 (100%); max |probability shift| per repeat: median 0.0000, max 0.0900.
- Reversed option order, same choice as base: 18/20 (90%).

### A7. Are the probabilities informative?

- A's p(choice) in [0, 0.6): n=0, A=B 0/0 (n/a)
- A's p(choice) in [0.6, 0.9): n=2, A=B 2/2 (100%)
- A's p(choice) in [0.9, 0.999): n=53, A=B 22/53 (42%)
- A's p(choice) in [0.999, 1.0): n=43, A=B 13/43 (30%)
(If nearly every answer is p >= 0.999, calibration metrics on the vector are near meaningless at this n; if low p predicts A/B disagreement, p is a usable triage signal.)

### A8. Tokens, latency, cost, model id

- Calls made 308 (ok 308); statuses {'ok': 308}; retries 0.
- Input tokens 391,106, output tokens 36,145; **cost $0.0164** at list price (pre-run estimate $0.0113); returned/estimated tokens median 1.73 (4 chars per token assumption).
- Latency p50 172 ms, p90 230 ms.
- Model ids returned: {'jev-1.13.0': 308}.

### A9. Mini gold sample for the owner to hand-label

16 listing ends in `gold_candidates.csv` (open the URLs, fill `owner_label` with one option name, before looking at the model columns if you can; they are at the right edge for that reason). Mix: A/B disagreements and unresolved first, then answers outside the rule_provision arm, then random agreements. With these 20-30 labels, model accuracy on A and B is a rough check, not a gate.

## Run 2: passage A = Form 25 fields plus the EX-99.25 notice (`out/summary.md`)

Copied as generated, headings demoted. This is the run the amendment's E1 to E14 cite (A1 to A12 below are its section labels).

Generated 2026-10-06T04:15:00+00:00; model requested `jev-1.13.0`; price snapshot $0.042/M input tokens; seed 20261005; quarters 2016Q2,2017Q4,2019Q1,2020Q3,2021Q2,2022Q4,2024Q1,2025Q2.

### A1. Population size and cost of the whole population

| quarter | index rows | originals | 25 | 25-NSE | amendments |
|---|---|---|---|---|---|
| 2016Q2 | 353 | 191 | 33 | 158 | 4 |
| 2017Q4 | 488 | 261 | 39 | 222 | 5 |
| 2019Q1 | 442 | 219 | 16 | 203 | 16 |
| 2020Q3 | 387 | 206 | 28 | 178 | 2 |
| 2021Q2 | 541 | 280 | 19 | 261 | 0 |
| 2022Q4 | 823 | 431 | 41 | 390 | 2 |
| 2024Q1 | 464 | 248 | 36 | 212 | 4 |
| 2025Q2 | 454 | 239 | 29 | 210 | 4 |

Mean 259 originals per sampled quarter, so about **11,153 listing ends 2016-01 to 2026-09** (spec assumed 'a few thousand', ~5,000). Mean estimated tokens per call: passage A 676, passage B 1,477. Whole population at list price: A only **$0.32**, B for every listing end **$0.69**, spec worst case (8,400 tokens each) $3.93.

rule_provision among fetched candidates: `12d2-2(a)(3)` 205, `12d2-2(a)(1)` 76, `12d2-2(b)` 72, `12d2-2(a)(2)` 68, `pre_xml` 56, `12d2-2(a)(4)` 3

### A2. Is 8-K context available?

- Sampled listing ends: 150; with an 8-K in [-45, +20] days and text extracted: 98/150 (65%).
- Context notes: ok: 86, no 8-K in window: 52, no relevant item segmented; body head used: 12
- By provision (n, with 8-K): `12d2-2(a)(1)` 37/27; `12d2-2(a)(2)` 37/17; `12d2-2(a)(3)` 37/24; `12d2-2(a)(4)` 3/3; `12d2-2(b)` 36/27

### A2b. Form 25 notice exhibit (EX-99.25) availability, by exchange

`text` = a notice with at least 20 words, included in passage A; `stub` = a file name or a one-liner (typical of Nasdaq), not included; `none` = no non-XML document.

| exchange group | n | text | stub | none | median chars (text) |
|---|---|---|---|---|---|
| NYSE family | 96 | 96/96 (100%) | 0 | 0 | 894 |
| Nasdaq | 50 | 23/50 (46%) | 27 | 0 | 766 |
| other | 4 | 4/4 (100%) | 0 | 0 | 667 |
| all | 150 | 123/150 (82%) | 27 | 0 | 888 |

By exchange name: Cboe BZX Exchange, Inc. `text` 4; NASDAQ Stock Market LLC `stub` 2; NASDAQ Stock Market LLC `text` 3; NEW YORK STOCK EXCHANGE LLC `text` 79; NYSE AMERICAN LLC `text` 5; NYSE ARCA, INC. `text` 11; NYSE MKT LLC `text` 1; Nasdaq Stock Market LLC `stub` 19; Nasdaq Stock Market LLC `text` 16; The Nasdaq Stock Market LLC `stub` 6; The Nasdaq Stock Market LLC `text` 4

Planned calls: 308 (A-base 150, B-base 98, B-repeat1 20, B-repeat2 20, B-reversed 20); estimated 338,169 input tokens, **$0.0142** (cap $5.0). EDGAR requests this run 0, cache hits 751.

### A3. Answer distribution and `unresolved`

- Passage A (n=150): unresolved 27/150 (18%); p(choice) < 0.6 8/150 (5%); p(choice) >= 0.999 88/150 (59%); median p(choice) 1.000.
  - `instrument_retirement` 71, `compliance_delisting` 30, `unresolved` 27, `merger_or_acquisition` 15, `bankruptcy` 6, `redomicile_or_reorganisation` 1
- Passage B (n=98): unresolved 6/98 (6%); p(choice) < 0.6 4/98 (4%); p(choice) >= 0.999 53/98 (54%); median p(choice) 1.000.
  - `instrument_retirement` 43, `merger_or_acquisition` 19, `compliance_delisting` 19, `bankruptcy` 9, `unresolved` 6, `redomicile_or_reorganisation` 1, `going_private` 1

### A4. Does the 8-K change the answer? (A vs B on the same listing end)

- Same option 72/98 (73%); same class 73/98 (74%).
- A `unresolved` -> B resolved: 20; A resolved -> B `unresolved`: 0; resolved-to-different-option flips: 6.
- Changes A -> B: `unresolved` -> `merger_or_acquisition` 10; `unresolved` -> `instrument_retirement` 9; `compliance_delisting` -> `bankruptcy` 5; `unresolved` -> `compliance_delisting` 1; `merger_or_acquisition` -> `going_private` 1

### A5. The deterministic `rule_provision` arm

Share of model answers inside the arm's option set (the arm reads one Form 25 field; if the model mostly lands inside it, passage A adds little beyond code).
- Passage A: inside arm 114/150 (76%); unresolved 27.
- Passage B: inside arm 89/98 (91%); unresolved 6.

| provision | n | passage A answers | passage B answers |
|---|---|---|---|
| `12d2-2(a)(1)` | 37 | instrument_retirement 32, unresolved 5 | instrument_retirement 26, unresolved 1 |
| `12d2-2(a)(2)` | 37 | instrument_retirement 27, unresolved 10 | instrument_retirement 13, unresolved 2, compliance_delisting 1, merger_or_acquisition 1 |
| `12d2-2(a)(3)` | 37 | merger_or_acquisition 15, unresolved 12, instrument_retirement 9, redomicile_or_reorganisation 1 | merger_or_acquisition 18, unresolved 3, instrument_retirement 1, redomicile_or_reorganisation 1, going_private 1 |
| `12d2-2(a)(4)` | 3 | instrument_retirement 3 | instrument_retirement 3 |
| `12d2-2(b)` | 36 | compliance_delisting 30, bankruptcy 6 | compliance_delisting 18, bankruptcy 9 |

### A6. Stability (same request repeated) and option order

- Identical choice across base + 2 repeats: 20/20 (100%); max |probability shift| per repeat: median 0.0000, max 0.0500.
- Reversed option order, same choice as base: 20/20 (100%).

### A7. Are the probabilities informative?

- A's p(choice) in [0, 0.6): n=3, A=B 2/3 (67%)
- A's p(choice) in [0.6, 0.9): n=10, A=B 5/10 (50%)
- A's p(choice) in [0.9, 0.999): n=29, A=B 18/29 (62%)
- A's p(choice) in [0.999, 1.0): n=56, A=B 47/56 (84%)
(If nearly every answer is p >= 0.999, calibration metrics on the vector are near meaningless at this n; if low p predicts A/B disagreement, p is a usable triage signal.)

### A8. Tokens, latency, cost, model id

- Calls made 308 (ok 308); statuses {'ok': 308}; retries 0.
- Input tokens 460,916, output tokens 36,211; **cost $0.0194** at list price (pre-run estimate $0.0142); returned/estimated tokens median 1.47 (4 chars per token assumption).
- Latency p50 175 ms, p90 241 ms.
- Model ids returned: {'jev-1.13.0': 308}.

### A10. Does the exhibit resolve on its own? Coverage

- A (Form 25 fields + notice exhibit) resolved: 123/150 (82%).
- **Coverage** (any resolved answer, A or B): 143/150 (95%); B adds 20 listing ends that A left unresolved.

| exchange group | exhibit | n | A resolved | with 8-K | coverage |
|---|---|---|---|---|---|
| NYSE family | text | 96 | 96/96 (100%) | 55 | 96/96 (100%) |
| Nasdaq | stub | 27 | 0/27 (0%) | 26 | 20/27 (74%) |
| Nasdaq | text | 23 | 23/23 (100%) | 17 | 23/23 (100%) |
| other | text | 4 | 4/4 (100%) | 0 | 4/4 (100%) |

By provision (A resolved / coverage): `12d2-2(a)(1)` 32/36 of 37; `12d2-2(a)(2)` 27/34 of 37; `12d2-2(a)(3)` 25/34 of 37; `12d2-2(a)(4)` 3/3 of 3; `12d2-2(b)` 36/36 of 36

### A11. Comparison with run 1 (passage A = Form 25 XML fields only)

- Same sample as run 1: yes.

| metric | run 1 | this run |
|---|---|---|
| calls (ok) | 308 (308) | 308 (308) |
| A unresolved | 150/150 (100%) | 27/150 (18%) |
| B unresolved | 37/98 (38%) | 6/98 (6%) |
| coverage (any resolved) | 61/150 (41%) | 143/150 (95%) |
| A = B (same option) | 37/98 (38%) | 72/98 (73%) |
| stable across repeats | 20/20 (100%) | 20/20 (100%) |
| reversed order same | 18/20 (90%) | 20/20 (100%) |
| mean input tokens, A | 814 | 1045 |
| input tokens / cost | 391,106 / $0.0164 | 460,916 / $0.0194 |

- Final answer per listing end, run 1 -> this run: run 1 unresolved -> resolved now 82; same final answer 59; different resolved answer 9
- Passage B answer unchanged (B now also carries the exhibit): 58/98 (59%).

### A12. Score on the gold rows

Gold file `~/tradepartner-probes/jev-spike/out-run1/agent_labels.csv` (16 rows). `agent_label` is the true reason (wider search allowed); `text_states` is what the Form 25 (with its notice) and the linked 8-K state. Final = B if B ran and resolved, else A.

| idx | issuer | exhibit | agent_label | text_states | A | B | final |
|---|---|---|---|---|---|---|---|
| 15 | MAIDEN HOLDINGS NORTH AMERICA, | text | instrument_retirement | instrument_retirement | instrument_retirement | - | instrument_retirement |
| 20 | Axar Acquisition Corp. | stub | instrument_retirement | instrument_retirement | unresolved | instrument_retirement | instrument_retirement |
| 22 | Prologis, Inc. | text | instrument_retirement | instrument_retirement | instrument_retirement | instrument_retirement | instrument_retirement |
| 29 | Royal Dutch Shell plc | text | instrument_retirement | instrument_retirement | instrument_retirement | - | instrument_retirement |
| 30 | EXA CORP | stub | merger_or_acquisition | merger_or_acquisition | unresolved | merger_or_acquisition | merger_or_acquisition |
| 46 | Scorpio Tankers Inc. | text | instrument_retirement | instrument_retirement | instrument_retirement | - | instrument_retirement |
| 47 | SELECTIVE INSURANCE GROUP INC | text | instrument_retirement | instrument_retirement | instrument_retirement | instrument_retirement | instrument_retirement |
| 51 | LILIS ENERGY, INC. | text | bankruptcy | bankruptcy | bankruptcy | bankruptcy | bankruptcy |
| 52 | Global Eagle Entertainment Inc | text | bankruptcy | bankruptcy | compliance_delisting | bankruptcy | bankruptcy |
| 64 | SEACOR HOLDINGS INC /NEW/ | text | merger_or_acquisition | merger_or_acquisition | merger_or_acquisition | merger_or_acquisition | merger_or_acquisition |
| 91 | Health Assurance Acquisition C | stub | instrument_retirement | instrument_retirement | unresolved | instrument_retirement | instrument_retirement |
| 95 | NightDragon Acquisition Corp. | stub | instrument_retirement | instrument_retirement | unresolved | instrument_retirement | instrument_retirement |
| 103 | iSHARES TRUST | stub | instrument_retirement | unresolved | unresolved | - | unresolved |
| 108 | ETF Managers Trust | text | instrument_retirement | unresolved | instrument_retirement | - | instrument_retirement |
| 125 | CURO Group Holdings Corp. | text | compliance_delisting | compliance_delisting | compliance_delisting | compliance_delisting | compliance_delisting |
| 147 | Customers Bancorp, Inc. | text | instrument_retirement | instrument_retirement | instrument_retirement | instrument_retirement | instrument_retirement |

- A vs agent_label: right 10/16 (62%), unresolved 5, wrong 1.
- A vs text_states: right 10/16 (62%), unresolved 4, wrong 2.
- B vs agent_label: right 11/11 (100%), unresolved 0, wrong 0.
- B vs text_states: right 11/11 (100%), unresolved 0, wrong 0.
- final vs agent_label: right 15/16 (94%), unresolved 1, wrong 0.
- final vs text_states: right 15/16 (94%), unresolved 0, wrong 1.

### A9. Mini gold sample for the owner to hand-label

25 listing ends in `gold_candidates.csv` (open the URLs, fill `owner_label` with one option name, before looking at the model columns if you can; they are at the right edge for that reason). Mix: A/B disagreements and unresolved first, then answers outside the rule_provision arm, then random agreements. With these 20-30 labels, model accuracy on A and B is a rough check, not a gate.

## Blind gold review, 16 listing ends (`out-run1/gold_review.md`)

Written by a Claude Code agent after run 1, not by the owner, untimed (amendment E10). The `model_B` column and the agreement figures below are run 1's; run 2's score on the same rows is A12 above (final answer 15/16 against `agent_label`).

Labelled blind first: `agent_labels.csv` was written before the model columns were opened. **agent_label** is the true reason, using wider search where needed. **text_states** is what the Form 25 (including its EX-99.25 notice text) plus the linked 8-K say on their own.

| idx | issuer | class | agent_label | text_states | model_B | agree? |
|---|---|---|---|---|---|---|
| 15 | Maiden Holdings North America | 8.25% Notes due 2041 | instrument_retirement | instrument_retirement | (not run, no 8-K) | n/a |
| 20 | Axar Acquisition Corp. | Common Stock (SPAC) | instrument_retirement | instrument_retirement | instrument_retirement | yes |
| 22 | Prologis, Inc. | 4.00% Notes due 2018 | instrument_retirement | instrument_retirement | unresolved | **no** |
| 29 | Royal Dutch Shell plc | guarantor, 1.250% Notes due 2017 | instrument_retirement | instrument_retirement | (not run) | n/a |
| 30 | Exa Corp | Common Stock | merger_or_acquisition | merger_or_acquisition | merger_or_acquisition | yes |
| 46 | Scorpio Tankers | 8.25% Senior Notes due 2019 | instrument_retirement | instrument_retirement | (not run) | n/a |
| 47 | Selective Insurance Group | 5.875% Senior Notes due 2043 | instrument_retirement | instrument_retirement | instrument_retirement | yes |
| 51 | Lilis Energy | Common Stock | bankruptcy | bankruptcy | bankruptcy | yes |
| 52 | Global Eagle Entertainment | Common Stock | bankruptcy | bankruptcy | bankruptcy | yes |
| 64 | SEACOR Holdings | Common Stock | merger_or_acquisition | merger_or_acquisition | merger_or_acquisition | yes |
| 91 | Health Assurance Acquisition | common, warrants, units (SPAC) | instrument_retirement | instrument_retirement | instrument_retirement | yes |
| 95 | NightDragon Acquisition | Class A, warrant, unit (SPAC) | instrument_retirement | instrument_retirement | instrument_retirement | yes |
| 103 | iShares Trust | iBonds Dec 2022 Term Treasury ETF | instrument_retirement | unresolved | (not run) | n/a |
| 108 | ETF Managers Trust | ETFMG 2X Daily Alternative Harvest ETF | instrument_retirement | unresolved | (not run) | n/a |
| 125 | CURO Group Holdings | Common Stock | compliance_delisting | compliance_delisting | compliance_delisting | yes |
| 147 | Customers Bancorp | Series E Preferred | instrument_retirement | instrument_retirement | instrument_retirement | yes |

### Agreement

model_B has an answer for 11 of the 16 rows. The other 5 have no 8-K link, so model_B was never run on them.

- **model_B vs text_states: 10 / 11 (91%)**
- **model_B vs agent_label: 10 / 11 (91%)**
- Disagreements: **1** (row 22)
- model_A (Form 25 alone) says `unresolved` on all 16. The agent reads a stated reason in the Form 25 notice text alone on 10 rows (15, 22, 29, 46, 147 redemption; 51 bankruptcy; 125 market cap; 64 merger; also 52's notice cites the listing rules). This suggests the model is seeing only the structured `primary_doc.xml` and not the EX-99.25 notice text, which is where the NYSE and NYSE American write the reason. Nasdaq's EX-99.25 is often an empty stub (rows 20, 30, 91, 95, 103), so for Nasdaq the 8-K is the only source. Worth checking in the spike's input builder.

### The one disagreement

**Row 22: Prologis, 4.00% Notes due 2018.** Prologis repaid these bonds early, in October 2017, a year before they were due. The NYSE's notice in the [Form 25](https://www.sec.gov/Archives/edgar/data/876661/0000876661-17-000633.txt) says so directly: *"the entire class of this security was called for redemption, maturity or retirement on October 18, 2017; ... funds sufficient for the payment of all such securities were deposited."* The 8-K the pipeline matched is Prologis's Q3 2017 earnings release ([link](https://www.sec.gov/Archives/edgar/data/1045609/000156459017019539/pld-8k_20171017.htm)), which does not mention the notes. So the model answered `unresolved`, a fair reading of that 8-K. The answer should be `instrument_retirement`: the bonds were called and paid off. The miss comes from the inputs, not the model's reasoning. The nearest 8-K was unrelated, and the Form 25 sentence that holds the answer appears not to have reached the model.

### Notes on judgement calls (agreements, but worth knowing)

- **64 SEACOR:** taken private by the PE fund American Industrial Partners for $41.50 cash per share. This is labelled merger_or_acquisition because there is an outside acquirer, not insiders. A reader who files sponsor buyouts under going_private would label it differently.
- **125 CURO:** the NYSE delisted it for market cap below $15M on 2024-03-11. CURO filed Chapter 11 on 2024-03-25 (8-K, [EDGAR index](https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0001711291&type=8-K)). The stated cause is compliance, but insolvency was the underlying driver.
- **103 / 108 (ETFs, no 8-K):** wider search, using the 497 prospectus supplements. The [iBonds fund](https://www.sec.gov/Archives/edgar/data/1100663/000119312522262944/d412780d497.htm) closed at its planned term date. The [ETFMG fund](https://www.sec.gov/Archives/edgar/data/1467831/000089418922008927/etfmgmjxlliquidationsupple.htm) was liquidated by a board decision, with cash at NAV. Both are labelled instrument_retirement. The ETFMG one is only medium confidence, because a discretionary fund closure fits none of the options exactly.

### Agent labels (`out-run1/agent_labels.csv`)

Written before the model columns were opened. Quotes are from public SEC filings.

| idx | agent_label | text_states | confidence | evidence quote | source |
|---|---|---|---|---|---|
| 15 | instrument_retirement | instrument_retirement | high | "the entire class of this security was called for redemption, maturity or retirement on June 15, 2016" | [filing](https://www.sec.gov/Archives/edgar/data/1512303/0000876661-16-001037.txt) |
| 20 | instrument_retirement | instrument_retirement | high | "in connection with the Company's previously announced redemption of all of its outstanding shares of common stock that were included in the units issued in its initial public offering" | [filing](https://www.sec.gov/Archives/edgar/data/1615892/000114420417049464/v475764_8k.htm) |
| 22 | instrument_retirement | instrument_retirement | high | "the entire class of this security was called for redemption, maturity or retirement on October 18, 2017" | [filing](https://www.sec.gov/Archives/edgar/data/876661/0000876661-17-000633.txt) |
| 29 | instrument_retirement | instrument_retirement | high | "the entire class of this security was redeemed or paid at maturity or retirement on November 10, 2017" | [filing](https://www.sec.gov/Archives/edgar/data/876661/0000876661-17-000697.txt) |
| 30 | merger_or_acquisition | merger_or_acquisition | high | "Upon completion of the Merger, the Company became a wholly owned subsidiary of Parent, which is an indirect wholly owned subsidiary of Dassault Systemes" | [filing](https://www.sec.gov/Archives/edgar/data/890264/000110465917069319/a17-26438_38k.htm) |
| 46 | instrument_retirement | instrument_retirement | high | "the entire class of this security was called for redemption, maturity or retirement on March 18, 2019" | [filing](https://www.sec.gov/Archives/edgar/data/876661/0000876661-19-000271.txt) |
| 47 | instrument_retirement | instrument_retirement | high | "the Company issued a press release announcing that it has given notice of its intent to redeem all of its outstanding 5.875% Senior Notes due 2043" | [filing](https://www.sec.gov/Archives/edgar/data/230557/000114420419011647/tv515010_8k.htm) |
| 51 | bankruptcy | bankruptcy | high | "based on the Company's June 29, 2020 announcement that it had filed voluntary petitions for a court-supervised reorganization under Chapter 11" | [filing](https://www.sec.gov/Archives/edgar/data/1437557/0001143313-20-000032.txt) |
| 52 | bankruptcy | bankruptcy | high | "Nasdaq reached its decision ... after the Company's disclosure on July 22, 2020 that the Company ... had filed for protection under chapter 11" | [filing](https://www.sec.gov/Archives/edgar/data/1512077/000119312520201025/d923104d8k.htm) |
| 64 | merger_or_acquisition | merger_or_acquisition | medium | "each share not previously purchased in the tender offer ... will be converted into the right to receive $41.50 per share, net to the seller in cash" | [filing](https://www.sec.gov/Archives/edgar/data/876661/0000876661-21-000556.txt) |
| 91 | instrument_retirement | instrument_retirement | high | "the publicly held Class A Common Stock ... will represent only the right to receive their pro-rata share in the Company's trust account, because the Company will not consummate an initial business combination" | [filing](https://www.sec.gov/Archives/edgar/data/1824013/000110465922112039/tm2229075d1_8k.htm) |
| 95 | instrument_retirement | instrument_retirement | high | "the Company will ... redeem 100% of the outstanding shares of the Company's Class A common stock that were included in the units issued in its initial public offering" | [filing](https://www.sec.gov/Archives/edgar/data/1837067/000119312522301015/d419356d8k.htm) |
| 103 | instrument_retirement | unresolved | high | "On December 3, 2019, in connection with the establishment of the Fund, the Board of Trustees of the Trust approved the planned liquidation, dissolution and termination of the Fund." | [filing](https://www.sec.gov/Archives/edgar/data/1100663/000119312522262944/d412780d497.htm) |
| 108 | instrument_retirement | unresolved | medium | "The Board of Trustees of the Trust has approved a Plan of Liquidation for the ETFMG 2X Daily Alternative Harvest ETF" | [filing](https://www.sec.gov/Archives/edgar/data/1467831/000089418922008927/etfmgmjxlliquidationsupple.htm) |
| 125 | compliance_delisting | compliance_delisting | medium | "the Company had fallen below the NYSE's continued listing standard requiring listed companies to maintain an average global market capitalization ... of at least $15,000,000" | [filing](https://www.sec.gov/Archives/edgar/data/1711291/0000876661-24-000162.txt) |
| 147 | instrument_retirement | instrument_retirement | high | "the entire class of this security was called for redemption, maturity or retirement on June 16, 2025" | [filing](https://www.sec.gov/Archives/edgar/data/1488813/0000876661-25-000447.txt) |

## Appendix: spec audit, assumptions and streamlining (`spec-audit.md`)

Written 2026-10-05, before either run; the amendment of 2026-10-06 (#1015) records which candidates were taken. Its section 3 (the owner's local run commands for the spike script) is omitted.

Read-only spike, 2026-10-05. Inputs: `docs/specs/research-labeling.md` (Draft, 250 lines, 18 reqs; plan sized L with about 15 build tasks and 4 owner tasks), ADR 0013, ADR 0008, #946 (smoke test, then the $40 budget decision), #956, #965, and the vendor's API and Models pages (read 2026-10-05: `POST /v1/systemone`, Bearer key, `choice` with a `criteria` map, response `model`/`answers`/`usage`, $0.042 per M input tokens, output free, no seed or temperature parameter documented). Script: `jev_sample.py` (offline harness `selftest_jev_sample.py`).

**The question the spec never asks:** the pilot's output is a shortlist one person reads. Its total vendor cost is about $1 to $11 even under pessimistic population sizes (A1 below), and the $40 budget leaves about a 4x margin over the worst case. Most of the spec's machinery protects against risks that are tiny at that scale (spend) or that the sample test can measure for under $0.10 (stability, order bias, the value of the 8-K, how informative the probabilities are). The parts that carry ADR 0013's real guarantees are research-only, no store writes, the owner decides, zero spend by default, and recorded inputs and outputs. Those are cheap to build, and the boundary tests (reqs 12 to 16) already hold them.

### 1. Load-bearing assumptions

| # | Assumption | What in the spec depends on it | Sample test (`summary.md` section) | If negated, cut or change |
|---|---|---|---|---|
| A1 | **The population is small**: "a few thousand" Form 25s since 2016, about 5,000 | Cost text ($1.76 for the population, $2.21 with margin), `batch_max_usd = 2.5` sized to fit one whole-population batch, "the whole population is labelled", the 12-batch budget | Counts the originals in 8 quarterly `form.idx` files and extrapolates (A1) | **Probably wrong, and in the direction that breaks a guard.** 25-NSE is filed for every notes, warrant, unit and rights line, plus the 2020-23 SPAC wave. I expect 2k to 3k a year, so 20k to 30k listing ends (unverified: I had no EDGAR User-Agent; the dry run prints the real figure). At the spec's own 8,400-token packet cap, 30k listing ends cost about $10.6, so the $2.50 batch cap refuses the whole-population batch the spec plans. **Fix:** size the cap from the measured count. Better, label only the provisions where the arm is ambiguous (A2). |
| A2 | **The 8-K context is needed beyond the Form 25's `rule_provision`** | The whole corpus fetch: context windows, 8-K item segmentation and priority list, `EX-99.1`, 8-K12B, the 45/20-day windows, the 8,000-token packet cap and truncation rule, char offsets and hashes | A vs B on the same listing end; share of answers inside the deterministic arm's set; A to B transitions (A4, A5) | The arm already gives exactly one class for `(a)(1)` and `(a)(2)` (instrument retirement), which I expect to be the bulk of 25-NSEs. If B changes the class mainly on `(a)(3)`, `(a)(4)`, `(b)` and `(c)`, **fetch 8-Ks only for those**, which cuts the corpus fetch and model calls by most of the population. If B rarely changes anything, drop the 8-K entirely and label passage A alone; but passage A without an 8-K mostly reduces to the arm, which says the model adds little. That is the B8 answer, and it is cheap to learn now. |
| A3 | **An 8-K exists in the window for most listing ends** | Packet design; the coverage limits in Risks | Share of the sample with an 8-K in [-45, +20] days, by provision (A2) | Notes, warrants, ETFs and foreign private issuers (6-K, not 8-K) will often have none. If the share is low outside `(a)(3)`/`(b)`, that is the same cut as A2. |
| A4 | **The 9-option set is exhaustive and `unresolved` is meaningful** | Option set object and hash, classes, crosswalk rows, the `unresolved_share <= 0.15` gate | Answer distribution, the `unresolved` share on A and on B, the share of low p(choice) (A3); owner mini-gold (A9) | If `unresolved` on B is above 15%, the gate fails before the pilot runs: revise the options or the passage first, not after a sealed score. If one option dominates (instrument retirement), the per-option precision gates are underpowered for most options, which the spec already concedes. |
| A5 | **The model is accurate enough that disagreements are mostly rule errors** | The shortlist's value; the 0.80 Wilson bar on 120 items | The 25-item mini gold gives a rough accuracy on A and on B (A9). It is not a gate. | The sample cannot settle this; only the gold sample or the shortlist yield can. What it can show is whether the model is obviously poor (say under 70% on the mini gold), which would stop the plan before about 15 PRs are built. |
| A6 | **The probabilities are informative** | ECE (15 equal-mass bins), multiclass Brier, classwise reliability, `vendor_confidence` stored separately, the formula test, `mean_abs_probability_shift` | Distribution of p(choice); share at or above 0.999; A=B agreement by p bin (A7). The #946 smoke test answered p = 1.0. | If almost every answer is about 1.0, **drop the calibration reporting and the `vendor_confidence` field**: it is derivable from the raw response, and calibration at n = 120 with degenerate probabilities is noise. If low p predicts A/B disagreement, keep p only as a shortlist sort key. |
| A7 | **Outputs are nondeterministic or drift, so a drift probe must gate every batch** | Drift set v1/v2 with baseline columns, a robustness registration, `--drift-set` gating step 2, the 0.15 flip bar at n = 100 | 20 items repeated twice: identical-choice rate and max probability shift (A6). The model id is recorded on every call. | If 100% of repeats are identical (likely for a classifier head), **replace the drift machinery with the existing model-id check plus 10 to 20 fixed packets re-sent at the start of a batch and compared in the same run**, with no registration and no versioned dataset. Cross-version drift cannot be tested today: only `jev-1.13.0` exists. |
| A8 | **First-option bias is material** (the vendor documents it) | Perturbation probe: 5 permutations over 30 dev items, a robustness registration, step 0's refusal of the pilot | Reversed option order on the 20-item subset: same-choice rate (A6) | If the flip rate is near 0, make it a one-off recorded check before the freeze, not a gating registered run. |
| A9 | **Cost matters enough for two ceilings, a batch cap, a margin, a runaway stop and a `research spend` command** | Req 8 entirely; parts of reqs 6, 7, 11 and 16 | Measured tokens and cost; the returned/estimated token ratio (A8) | **At $0.042/M, the full population at the spec's worst-case packet is about $1 to $11. Typical packets are about $0.5 to $4. This spike is about $0.03.** One ceiling (the pilot total, now $40), checked against Σ `cost_usd` from the records before each batch, plus a mid-batch stop, covers ADR 0013 point 7 and owner decision 2. The monthly key can simply equal the total. |
| A10 | **4 characters ≈ 1 token** | Pre-call estimate, the runaway stop at 2x | Median ratio of returned to estimated tokens (A8) | If the ratio is far off, the runaway stop at 2x fires falsely. Fix the divisor; no machinery needed. |
| A11 | **EDGAR fetch pace is a constraint worth a separate fetch module, build dirs and a manifest** | `tradepartner.corpus.departure_fetch`, copy-to-build-dir (to survive `_fetch_delisting`'s unlink), the count identity | The dry run prints EDGAR requests and cache hits | At about 3 requests per listing end (Form 25 `.txt`, submissions JSON, one 8-K), 25k listing ends is about 75k requests: about 4 h at 5/s, once, then cached. A plain cache directory of its own, outside `edgar.cache_dir`, avoids the unlink problem without build ids. Restricting 8-Ks to the ambiguous provisions (A2) cuts this by more than half. |
| A12 | **Disagreements are frequent enough to justify a shortlist with agreement sampling, deferral and `max_items`** | Req 9's shortlist, `agreement_sample_size`, stratum weights, `deferred` carry-over | **Not testable here**: it needs the rule answers from the owner's store, which this spike was told not to touch. Proxy: the share of model answers outside the arm's set (A5). | If the outside-arm share is small (a few percent of 25k is still hundreds), `max_items` and deferral matter. If it is tiny, the shortlist is the whole review and the stratum weights are overkill. |
| A13 | **150 gold items at 1.5 to 2 minutes each fit 3 to 5 hours** | Labelling-aid TUI design, the session file, `--lock` | The owner labels the 25-item mini gold with a stopwatch | If it is 1 minute a case with a plain sheet, the TUI is unnecessary. If it is 3 or more minutes, cut the gold to the ambiguous provisions (this changes the estimand; the owner decides). |
| A14 | **One-per-CIK unstratified random gold is the right estimand** | Draw rule, `min_clusters = 90`, the per-option gates mostly underpowered | Provision mix in A1 shows how much of a random draw lands on `(a)(1)`/`(a)(2)`, where the arm alone is right | If about 70% are retirements, a random 120 spends most of the owner's labels on cases code already answers, and the bar measures the easy stratum. Consider stratifying by provision with reported weights, as #965 F6 already anticipated. |

### 2. Streamlining candidates, ranked by build saved and risk taken

"Keep" is what protects point-in-time integrity and ADR 0013's boundary. Ranked by how much plan they remove per unit of guarantee lost (none lose a listed guarantee unless noted).

1. **Five registrations, eight dataset names and the dataset-version plumbing → one registration and one run per batch.** It protects reproducibility, and ADR 0005's "no id, no run". Minimal: one `departure-reason-pilot` registration; every batch, probe or recording is a run under it whose `config_json` holds the option-set hash, model id, packet rule version, and the content hashes of the corpus file and the records file. Drop the `fixtures`, `probes` and `batches-prospective` registrations (prospective waits until a forward batch exists) and the separate dataset versions for frame, drift set, inferences and reviews. Keep: a run row exists before the first call; hashes are recorded. *Saves several tasks and most of reqs 10 and 11.*
2. **Separate corpus fetch + frame build + register pipeline → one corpus script plus one label command.** It protects the rule "research imports no adapter" and point-in-time. Minimal: `corpus fetch` (outside `research`) writes one JSONL or parquet of listing ends with Form 25 fields, the chosen 8-K's items text, accessions and acceptance times, into its own cache dir. The label command reads that file only. The rule answer is computed by a small read-only function at a recorded `t`. Keep the import boundary, `known_at` from acceptance, and the count printout. Drop build ids, the copy-to-build-dir, char offsets, `counts.json` identity tests beyond a printed count, and sentence-boundary truncation priority lists.
3. **Drift probe (versioned drift set, baseline columns, robustness registration, gate before each batch) → model-id check plus a 10 to 20 packet repeat within the batch run.** Conditional on A7 showing identical repeats. Keep the returned-model-id stop; it is the real protection against a silent re-route the vendor reports.
4. **Spend: two ceilings + batch cap + margin + runaway + `research spend` command → one ceiling from records plus a hard stop.** It protects the credit, and zero spend by default. Keep: defaults `0.0` in code, the non-zero value only from `.env`, "cannot connect at zero" (test (e)), cost summed from records. Drop the batch cap, the margin config, `research spend` (one `jq`/DuckDB line over the JSONL answers it), and the separate total-versus-month keys (set both to $40, or one key).
5. **Perturbation probe as a gating registered run with step-0 refusal → a one-off reversed-order check recorded on #963.** Conditional on A8.
6. **Test doubles: Scripted + Recorded + a registered owner recording run with 8 hand-built packets → one scripted stub plus one owner-recorded response file.** The spike's `out/calls.jsonl` contains real raw responses made with the owner's key. A few of them can become `tests/fixtures/typesafe/*.json` by an owner copy, satisfying ADR 0008 point 3 (agents never hand-write them). Drop the `record-fixtures` command, its registration and the fixture-packets dataset.
7. **25-field per-call record → about 14 fields.** Keep: `record_id`, `run_id`, `listing_end_id` (accession), `passage_sha256`, `option_set_hash`, `model_id_requested`, `model_id_returned`, `raw_request` (body only), `raw_response`, `selected_option`, `probabilities`, `input_tokens`, `cost_usd`, `http_status`/`reason`, `known_at` (UTC). Drop or derive: `vendor_confidence` (in raw), `request_sha256`, `client_version`, `document_ids`, `source_available_at` (it is in the corpus row), `inference_started_at`, `attempt` (retries are rare; log them).
8. **8-K item segmentation, priority order, EX-99.1, 8-K12B, marker windows → submissions-JSON `items` plus a simple heading split, only for ambiguous provisions.** Conditional on A2 and A3. The script shows the minimal version works in under 60 lines. The Form 15 and 8-A12B markers stay, because the crosswalk's row 4 needs them, but they come from the same submissions JSON with no download.
9. **Labelling-aid TUI (keystrokes, skip queue, paragraph-number offsets, `--sheet`, `--lock`) → a static HTML or CSV sheet plus "lock = hash the filled CSV and record it on #963 before the first model call".** It protects blind-first gold. Blind-first is enforced by order (gold locked and hashed before the pilot run opens), not by a TUI. `gold_candidates.csv` is the template. Measure the time with A13 before building anything.
10. **Review screen with seeded a/b order, class-description rendering and quoted-text hashing → the same sheet: passage, filing URLs, two unattributed class answers, a decision column.** Keep unattributed answers (ADR 0013 point 4) and the review file as the only decision record. Drop offset hashing: accession plus a quoted sentence is enough for a fix PR.
11. **Calibration reporting (ECE, Brier, classwise reliability) → drop if A6 shows degenerate probabilities.** They are reporting-only anyway.
12. **Keep as is (cheap and load-bearing):** the boundary tests (reqs 12 to 16; perhaps trim test (b)'s `__import__` and relative-import edge cases to what exists), the zero default, `SecretStr` and redaction, the pinned-id regex, the option set as a hashed object, classes and the arm as pure functions, the pre-registered bar with one sealed score (the registry already exists), the terms line, and "a fix is a PR citing the decision".

**Net effect if A2, A6, A7 and A8 come back as I expect:** the plan drops from L (about 15 tasks, a chain of 7 to 8 PRs) to M (about 6 tasks): boundary tests; config, secret and client; corpus script; packets, crosswalk and label command; gold and review sheets plus scoring; docs (terms line, $40 budget text, one experiment file). Candidates 1, 2, 4, 6, 7, 9 and 10 do not depend on the sample at all; they follow from the scale.

**Needs an ADR 0013 amendment, not just a spec edit (flag only):** ADR 0013 point 5 makes a passed gold-label pilot a precondition for any shortlist, and requires a drift probe "re-sent at the start of every later run". Candidate 3 fits the letter of the latter (a repeat inside the run). Replacing the gold gate with "the owner reads the first shortlist and the yield decides" would be an amendment. I do not recommend it before A5 is known.
