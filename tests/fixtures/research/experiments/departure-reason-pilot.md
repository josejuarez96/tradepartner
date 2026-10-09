# Experiment: Departure reason, pilot A: class accuracy of the pinned model against blind gold labels

**Kind:** benchmark  ·  **Stage:** 3  ·  **Author:** team labelcli (plan T124, #1348), for the owner  ·  **Date:** 2026-10-09

Merging this file does not register it. The owner runs `tradepartner experiment register docs/experiments/departure-reason-pilot.md` after the gold session is locked (plan "Owner items" step 5). The registry hashes the whole file. Values that exist only once the frame and the gold session do (the frame's dataset id, SHA-256 and `t`, the `dev` boundary, the exclusion counts) are not carried here: code writes them into `session.json`, the export's metadata and every run's `config_json`. Any edit of this file before registration (the seeds' crosswalk rows below, #1351) is an owner-merged PR, so the registered hash is a file on `main`.

## Rationale

ADR 0013 lets a pretrained classification model label a code-selected passage of a public filing with one option from a set we fix, so that a job can compare the label with what our rules concluded and hand the owner a shortlist of disagreements. This registration is the gate on that shortlist: no batch is opened until its `TP-` claim says `pass` (ADR 0013 point 5; spec req 10). It answers one question: on listing ends drawn at random, one per issuer CIK, does the pinned model name the class of the departure reason the owner reads in the issuer's filings at least 80% of the time, at the lower bound of a one-sided 95% interval?

- **Treatment.** The pinned model `jev-1.13.0` (never an alias; `research.models.check_model_id`) asked the one `choice` question of `tradepartner.research.labeling.questions`, option set version `1`, SHA-256 `56a8b4b0d7bf78a194ff801637ef8d0fc6c4c6666f93c63cd85ee1e30eb511f4`, over the packets `research.labeling.packets` builds: packet B (the Form 25 fields, its notice exhibit when it is text, and the one 8-K) first where the listing end has an 8-K, then packet A (the Form 25 fields and the notice) only when B answers `unresolved`; packet A alone where there is no 8-K. The listing end's label is the last call's answer (spec amendment C2).
- **Outcome.** The owner's gold label (`gold_label`): the true reason from the issuer's EDGAR filings in the window, made blind on the review page before any model output exists for the gold items (owner decisions 3 and 6 of 2026-10-06; spec C10), with `text_states` recorded where the shown text says less.
- **Primary metric.** `class_accuracy`: the share of scorable `pilot` items whose model class equals the gold class. A model `unresolved` and a `timeout` on a labelled item count as wrong. Gold `unresolved`, `unlabelled` items and seeds outside the random draw are not in the denominator.
- **Claims.** ER-14 (typed decision models classify financial disclosures accurately and in a calibrated way; INSUFFICIENT) and ER-15 (option-name following; UNGRADED). The verdict is recorded as a `TP-` claim, exploratory (research program §6). Backlog B8's question, whether reading the filing adds anything to a field code already holds, is answered by the `rule_provision` arm below on the same items.

## Prior-evidence disclosure

- The read-only sample test of 2026-10-05/06 (`~/tradepartner-probes/jev-spike/`, two runs of 308 calls at `jev-1.13.0`, $0.036, outside the repo; spec amendment evidence E1 to E14): 150 listing ends stratified by `rule_provision`, model answers on all of them, and 16 cases labelled blind by an agent (not the owner), 15 of 16 right on the final answer (E10). Those 150 accessions' issuers are removed from the gold draw (the exclusion below), so no gold item has model output (ADR 0013 "Owner decisions 2026-10-06 (#1026)" decision 2).
- The owner has read the sample test's two summaries, which name issuers; the exclusion removes every listing end of those issuers' CIKs.
- The seeded error set (below) was chosen by issue history, so it is outside the accuracy denominator.
- No other result on this population, split or outcome has been seen.

## The gold sample (req 10, owner decision 3 of 2026-10-05, C10)

- **Frame.** `departure-reason-frame`, built by `tradepartner research frame build departure-reason --corpus <corpus.jsonl> --as-of <t> --register` after T45b's real-store run (plan T126). Its dataset id, SHA-256 and `t` (`rule_as_of`) are in `session.json` and every run's `config_json`.
- **Draw.** `tradepartner research gold --frame <frame id> --seed 20261009 --n 150 --exclude <exclusion file>`: 150 listing ends by a seeded random draw, stratified by period only, **one per CIK** (within each period the listing ends are permuted under the seed and the first listing end of each not-yet-drawn CIK is kept), 30 from `dev` and 120 from `pilot`. No `rule_*` column, `rule_provision` value or model output takes part (`gold.build_gold_session` reads only `listing_end_id`, `cik` and `form25_accepted_at`).
- **The registered seed.** `20261009`, published by this file and cited on #963 before the frame is built. The same integer is `seed` below and is the `--seed` of `research gold`.
- **Splits.** `dev`: the 30 items from the earliest acceptance span holding at least 300 frame rows (the boundary day is computed by `gold.build_gold_session` from the frame's counts and written to `session.json`). `pilot`: the 120 items after that boundary up to the frame's last acceptance, sealed by that period when `research gold` registers the locked export (`departure-reason-gold`, `--locked`, sealed split `pilot`).
- **Exclusion.** The sample test's `out/sample.csv` (150 accessions, its `accession` column; its issuer CIKs in the `cik` column the owner adds from the file's `issuer_cik` if the build asks for one, spec T126). SHA-256 of the file as it stands on 2026-10-09: `86dbc78f85092b85b4847a62f2972952c6191ce0c17e830c48e04e904ad74684`. The hash that binds is the one `research gold` prints and writes into `session.json` and the export's metadata; if the owner edits the file first (the `cik` column), this line is updated to that hash by an owner-merged PR before registration. The three counts `research gold` prints (accessions matched, issuer CIKs, rows removed) are in `session.json`.
- **Scorable count.** `research gold` prints the scorable `pilot` count when it locks the session; it is recorded on #963 before the first `pilot` run opens. Fewer than `min_clusters` (90) scorable items makes the one sealed score `underpowered`, so the `pilot` run is not opened in that case.

### Seeded error set

Added beside the 150, outside the one-per-CIK rule, flagged `seed_case`, scored by `seeded_recall` and outside the accuracy denominator unless the random draw itself picks one (it then counts in both). A seed whose Form 25 the frame does not hold is dropped and noted at registration. Each seed's **pre-fix rule answer** is the output of the pre-fix rule function at the fix's parent `code_version`, as traced on its issue; the crosswalk row that output gives is written into `config_json` with that commit at registration. **Open (#1351):** no command adds the seeds to the gold session yet and `seeded_recall` is not computed against these answers; and on the traced rows 4 or 5 a correct label is an agreement, so the `seeded_recall = 1.0` gate below waits on the owner's decision there. The traced reading from each issue:

| Seed | Issue | Form 25 | Parent `code_version` | Pre-fix behaviour as traced on the issue | Pre-fix rule answer (crosswalk reading) |
|---|---|---|---|---|---|
| Valaris (Class A, `0000314808`) | #921 | 25-NSE filed 2020-09-04, effective 2020-09-14 | `f2b5261` | the footnote alias `VAL*` folded into `VAL` kept the Form-25-delisted span alive; the post-emergence common became a plain class of the old company, not a `<cik>@<date>` successor | the line the rule did not end, no successor: row 6 |
| Seagate 2021 (`0001137789`) | #943 | the Ireland redomicile, effective 2021-05-28 | `ca79641` | a repair dry run would cut the live STX line (1,714 bars): the redomicile and relisting filings read as an end | delisted, no relisting, no successor: row 4 or 5, by the Form 15 marker |
| MiMedx 2019 (`0001376339`) | #943 | Nasdaq delisting effective 2019-03-08 (relisted 2020-11) | `ca79641` | the long-gap relisting (#905's accepted cost) cut the 2020 relisting off the same security | delisted, no relisting: row 4 or 5, by the Form 15 marker |
| Sotheby's 2019 (`0000823094`) | #905 | 25-NSE effective 2019-10-13 | `6d0b17b` (the parent of #905's fix) | a debt cover page after the stopped line kept the delisted `BID` span alive | the line the rule did not end: row 6 |
| Match Group 2020 separation (`0000891103`) | #828, #922 | the July 2020 IAC/Match separation | `f2b5261` | the #835 rules no longer derive the owner-accepted successor `0000891103@2020-08-10` | delisted, no successor: row 4 or 5, by the Form 15 marker |

Seagate 2010 is outside the population and is not a seed; #893's rename holes have no Form 25 and are not seeds.

## The tables (registered by hash; changing any is an amendment)

**Option set** (`questions.DEFAULT_OPTION_SET`, version `1`, the order sent): `merger_or_acquisition`, `going_private`, `instrument_retirement`, `redomicile_or_reorganisation`, `bankruptcy`, `compliance_delisting`, `voluntary_withdrawal`, `exchange_transfer`, `unresolved`.

**Classes** (`crosswalk.CLASSES`, a partition of the eight non-`unresolved` options; scoring compares classes):

| Class | Options |
|---|---|
| `transfer` | `exchange_transfer` |
| `continuity` | `redomicile_or_reorganisation` |
| `insolvency` | `bankruptcy` |
| `terminal` | `merger_or_acquisition`, `going_private`, `instrument_retirement` |
| `removal` | `compliance_delisting`, `voluntary_withdrawal` |

**Crosswalk** (`crosswalk.ROW_CONSISTENT_OPTIONS`: the rule answer at `t` onto the options consistent with it; a label outside the set is a disagreement, `unresolved` is neither):

| Row | Rule answer (status, relisted same security, successor, Form 15 in window) | Consistent options |
|---|---|---|
| 1 | `transferred` | `exchange_transfer` |
| 2 | `delisted`, the same security relisted after the effective day | `redomicile_or_reorganisation` |
| 3 | `delisted`, a successor security created | `bankruptcy`, `redomicile_or_reorganisation` |
| 4 | `delisted`, a Form 15 in the reorganisation window, no relisting, no successor | `merger_or_acquisition`, `going_private`, `redomicile_or_reorganisation`, `instrument_retirement` |
| 5 | `delisted`, none of the above | `merger_or_acquisition`, `going_private`, `compliance_delisting`, `voluntary_withdrawal`, `instrument_retirement`, `bankruptcy` |
| 6 | `listed` after the effective day | none: a disagreement by construction |
| 7 | `unmatched` (no `delistings` row joins the filing) | none: a disagreement by construction |

**`rule_provision` arm** (`crosswalk.RULE_PROVISION_ARM`, from 17 CFR 240.12d2-2 (a)(1) to (a)(4), (b), (c); the comparison set):

| Provision | Options |
|---|---|
| `12d2-2(a)(1)`, `12d2-2(a)(2)` | `instrument_retirement` |
| `12d2-2(a)(3)` | `merger_or_acquisition`, `going_private`, `redomicile_or_reorganisation`, `bankruptcy` |
| `12d2-2(a)(4)` | `instrument_retirement`, `bankruptcy` |
| `12d2-2(b)` | `compliance_delisting`, `bankruptcy` |
| `12d2-2(c)` | `exchange_transfer`, `voluntary_withdrawal` |
| anything else | `unresolved` |

## The bars (req 10, owner decision 5 of 2026-10-05)

- **Primary.** `class_accuracy` on `pilot`, `direction = greater`, `threshold = 0.80`, Wilson score one-sided 95% lower bound, one item per CIK, unweighted. The bars are lower bounds, so the smaller sample is stricter: at n = 120, 102 right (0.85) gives a lower bound of 0.789 and **fails**; 104 right (0.867) gives 0.807, the smallest pass; 108 right (0.90) gives 0.846. With the model `unresolved` on about 5% of listing ends (E12), the smallest pass needs it right on about 91% of the cases it resolves.
- **Secondary, gating through the stop rule.** `per_option`: precision and recall with one-sided 95% Clopper–Pearson lower bounds, gate `precision_lb >= 0.60` for every option with at least 20 gold items (fewer: `underpowered`, reported, not gated); `seeded_recall = 1.0` (every seed on the shortlist under its pre-fix rule answer); `unresolved_share <= 0.15` on `pilot`; `unlabelled_share <= 0.05` on `pilot`, with `worst_case_lower_bound` (every unlabelled item counted wrong) reported beside the primary and quoted in the `TP-` claim; `class_accuracy_text_states`, the same score against `text_states`, reported beside it.
- **Reported, never gating.** ECE with 15 equal-mass bins, multiclass Brier (summed over classes), classwise reliability, all on the probability vector (never on `vendor_confidence`); the share of answers with p(choice) >= 0.999 and agreement by the four probability bins of E7; the `rule_provision` arm's coverage and class accuracy on the same items; the reversed-order pass's `flip_rate` and `unresolved` share (run under `departure-reason-drift` once a command can run it, #1351); p50 and p90 latency; cost.
- **Runs.** At most two `dev` configurations (option-set versions within `budget.configurations`), the freeze, then the **one** `pilot` score: `tradepartner research label departure-reason-pilot --dataset <gold id> --split pilot --model jev-1.13.0 --spend-holdout --holdout-reason "<the one sealed score>"`, a flagged holdout spend; a second scoring of the sealed period is a flagged repeat. Every run is `model_historical`, exploratory forever (research-registry req 2; ML brief §7).

**Window.** `[window] end = 2026-12-31` stands in for spec req 10's "to the freeze": the freeze date does not exist when the file is merged, and the registry needs a literal date. What keeps post-freeze filings out is the frame: it is built once at `t` before the freeze (the frame drops every listing end accepted after `t`), the freeze date is recorded on #963, and a frame rebuilt after the freeze is bound by a prospective registration (spec C7), never by this one.

## Parameters

```toml experiment
slug = "departure-reason-pilot"
kind = "benchmark"
stage = 3
title = "Departure reason, pilot A: class accuracy of the pinned model against blind gold labels"
confirmatory = false
provenance = "model_historical"
touches_returns = false
claims = ["ER-14", "ER-15"]
hypothesis_ref = "B8"
seed = 20261009
splits = ["dev", "pilot"]

[dataset]
name = "departure-reason-gold"

[window]
start = 2016-01-01
end = 2026-12-31

[primary]
metric = "class_accuracy"
direction = "greater"
threshold = 0.80
ci_level = 0.95
min_clusters = 90
inference = "Wilson score one-sided 95% lower bound (one item per CIK, unweighted)"
secondary = ["per_option", "seeded_recall", "unresolved_share", "unlabelled_share", "worst_case_lower_bound", "class_accuracy_text_states"]
comparison_set = "the rule_provision arm (17 CFR 240.12d2-2 paragraph mapped to an option set by code) on the same gold items: coverage and class accuracy"

[multiplicity]
method = "none"

[budget]
runs = 6
configurations = 2
stop_rule = "a missed gate (primary lower bound under 0.80, a powered option's precision lower bound under 0.60, seeded_recall under 1.0, unresolved_share over 0.15 or unlabelled_share over 0.05 on pilot) is a negative result and closes task A; the option set may be narrowed in a new registration, the bars are never lowered after a pilot score"
expected_effect = "class accuracy 0.80 to 0.90 (a factual reading of a stated reason; the sample test's final answer was right on 15 of 16 agent-labelled cases); the rule_provision arm alone covers most terminal items and none of the insolvency-versus-removal or continuity-versus-terminal splits"
```

## Expected magnitudes and red flags

- `class_accuracy` above 0.97 on `pilot`, or every per-option precision at 1.0: check that no gold item has model output (the exclusion counts, the blindness refusal) and that the packet carries nothing but the filing text.
- `unresolved_share` above 0.15: the two-passage rule is not running (the sample test had 5% with both packets, 18% with packet A alone).
- The `rule_provision` arm's coverage far under the sample test's 76% (packet A) or 91% (packet B): the provision field is not reaching the arm.
- A returned model id other than `jev-1.13.0`: the run stops (`model id mismatch`); the stream ends and a new pinned id is a new registration.
- Spend above about $0.10 for the gold runs: the token estimate or the packet cap is off (whole pilot estimated at about $1, under $5 worst case, against the owner's $40).

## Retirement condition

A `pilot` score that misses any gate retires this design and closes task A with a negative `TP-` claim; a narrower option set is a new registration with a new slug, never these bars lowered. A vendor retirement of `jev-1.13.0` before the `pilot` score ends this registration too.
