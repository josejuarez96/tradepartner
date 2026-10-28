# Gate 0: the external discovery report against what we already have

**Date:** 2026-10-27 · **Issue:** #2409 · **Source:** [the external report](2026-10-27-discovery-system-external.md) (citations unverified), section 7, Gate 0 · **Type:** reconciliation, no new evidence

The owner adopted Gates 0 and 1 of that report (Gate 1 is [research-program stage 4a](../ways-of-working/research-program.md#4a-feasibility-check-before-an-experiment-file)). Gates 2 to 5 (automation, a four-arm benchmark, grammar or GP search, RL) are not adopted; the report's own section 6 says not to build them yet.

## What maps where

| Report asks for | Already here | Verdict |
|---|---|---|
| Treat the 10-19 discovery report as predecessor | [2026-10-19-strategy-discovery](2026-10-19-strategy-discovery.md): landscape, search-process accounting, rules L1 to L5 | Kept; its ledger is the base of the idea ledger |
| Negative evidence carried into claims | [claims.toml](claims.toml) `TP-` entries (TP-5 B3, TP-6 B9, TP-7 long/short), research-program stage 6 | Exists for backtests and ladder verdicts; **missing for feasibility deferrals and "killed on survivor data"** (C1, C2 had no entry) |
| Machine-readable idea memory | The [ladder ledger](hypothesis-backlog.md#ladder-ledger) (one row per idea) plus the backlog's items and Parked table | Exists as Markdown tables; the report's "typed file" is this. No new database |
| Count every look, failed or not | Store trial registry ([trial-registry.md](trial-registry.md), `hypotheses`/`trials`) and research-run registry (`store/research.py`) count runs; ledger L1 to L3 count rung 1-to-3 reads | Exists. Report's open question 2 (probes never counted) is what L1 closes for new work |
| Frozen development boundary, protected exams, forward paper exam | [ADR 0016](../decisions/0016-development-boundary-and-forward-exams.md); research-program Zones; the lab refuses windows past the boundary | Exists; adopted unchanged (the report's 2023-12-29 is our boundary) |
| Experimental families as controls | `config.FAMILIES`: `momentum`, `oracle` (test-only), `profitability`, `combined`, `filing_delay`, `spinoff`; the last two are `engine_ready=False`, research-run only | Exists; family N carries the search (C1b file: "family N is not reset by a better design") |
| Mechanism-first, event-based generation with kill ladder | [ADR 0023](../decisions/0023-break-first-fast-books-paper-exam.md) ladder; [sharper candidates](2026-10-27-sharper-candidates.md); rung 2-O | Exists; this is the report's Baseline B |
| Feasibility router before any backtest | Ladder rungs 1 to 3 (cost, published leg, capacity) | **Partial.** Rungs cover cost and capacity. Not covered before an experiment file: causal timestamp, event counts, power, matched benchmark. That is Gate 1, now research-program 4a |
| Candidate-generation arms, blinded benchmark, allocator, GP, RL, agents | none | Not adopted (Gates 2 to 5); revisit only if the ledger shows idea volume above its own upgrade trigger (more than one item a month at rung 1, or a batch above 20 ideas) |

## The idea ledger: the report's section 3A fields

The ledger is the existing [ladder ledger](hypothesis-backlog.md#ladder-ledger) Items table. Mapping each field:

| Report field | Where it lives | Gap |
|---|---|---|
| `idea_id` | ledger `id` (backlog id, or `L-<n>`); claim ids for results | none |
| origin; author/model | ledger `origin` (paper / extension / original; proposer, batch id) | none |
| creation timestamp | ledger `date` | none |
| sources | `origin` plus `link` to the note/report; claims carry `source` | partial: no field for the papers behind an idea except through the linked note |
| market mechanism | ledger `mechanism` | none |
| target universe, horizon, target variable | the `declared read` cell for ledger ideas; the experiment file for built ones | **missing as fields**; left in the declared read and the file, not added (more columns than rows warrant) |
| preliminary looks | ledger `looks` | none |
| constraints | ADR 0024 universe rules, rung 3 (capacity at $100) | not per idea; left to the note |
| relatives / duplicates | none | **missing**; add to `links` when a new row is a variant of an old one |
| decision | ledger `rungs` (furthest rung and verdict) | **partial**: it mixes status and rung; now a separate `decision` column |
| feasibility result | none | **missing; added** as the `feasibility` column (research-program 4a) |
| links to claims, experiments | ledger `link` (notes only) | **partial**: now also experiment file, claim id and issue |
| theoretical / observed / untested | claim `kind` and `grade`; ledger `looks` = 0 means untested | none |

**Gaps, five lines.** (1) Feasibility result: missing, added. (2) Decision as its own value (`open`, `deferred: <reason>`, `retired`, `parked`, `promoted`): added. (3) Links to experiment file and claim: added to `link`. (4) Duplicates and relatives: no field; named in `link`. (5) Universe, horizon and target variable: not fields; they stay in the declared read and the experiment file. No new store, schema or script.

## Report claims not carried

The report's numbers on C2 (12 readable events at six months, 8 at twelve) are right and match the [C2 design](../experiments/c2-spinoff-children.md). Its literature figures (McLean and Pontiff 26% and 58%, and the rest) are the same family as claims QI-2 and G2-S10 already in the register; they were not re-checked. The two 2026 preprints are UNVERIFIED and enter nothing.
