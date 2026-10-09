# Experiment: Departure reason, labeling batches: the yield of the owner's shortlist review

**Kind:** benchmark  ·  **Stage:** 3  ·  **Author:** team labelcli (plan T124, #1348), for the owner  ·  **Date:** 2026-10-09

Merging this file does not register it. The owner runs `tradepartner experiment register docs/experiments/departure-reason-batches.md` with the other two (plan "Owner items" step 5). No batch run is opened until `departure-reason-pilot`'s `TP-` claim says `pass` (ADR 0013 point 5: "no task produces a shortlist for the owner to act on before it has passed").

## Rationale

Each batch labels listing ends of the frame with the frozen configuration of `departure-reason-pilot`, compares every label with the rule answer at `t` through the crosswalk, and hands the owner a shortlist: every disagreement, every `unresolved`, and a seeded sample of the agreements (ADR 0013 point 4; spec req 9). The owner decides each item on the review page, blind to which answer is whose, and a fix enters the system only as a reviewed PR citing the review file's hash and the accession (ADR 0013 point 4; ADR 0008 point 3). One batch and its review are **one run** (`benchmark`; spec req 10, #956 item 4): the system is the pinned configuration, the outcome the owner's decisions.

- **Treatment.** The frozen configuration: `jev-1.13.0`, option set version `1` (SHA-256 `56a8b4b0d7bf78a194ff801637ef8d0fc6c4c6666f93c63cd85ee1e30eb511f4`), the packet rule of C2, `research.labeling` limits as recorded in the run's `config_json`.
- **Input.** `departure-reason-frame`, split `full` (every row). Its inference records are an **output** dataset version (`departure-reason-inferences`) named in the run's result, and the review file another (`departure-reason-reviews`): the run binds its input frame, as the spec's "Settled" paragraph states.
- **Before each batch.** `research label` runs the drift probe of `departure-reason-drift` under its own run and closes the batch `failed` (`drift probe not passed`) before its first call unless that probe passes; the model-id check stops any call that comes back from another id.
- **Command.** `tradepartner research label departure-reason-batches --dataset <frame id> --split full --model jev-1.13.0 --drift-gold <gold id> --drift-baseline-run <frozen dev run> [--accepted-from] [--accepted-to] [--limit]`, then `tradepartner research review --run <id>`, which opens the page on the shortlist and finishes the run when the last item is decided.
- **Claims.** ER-14 and ER-15 (topic 9b), as for the pilot; each batch's verdict is recorded by the owner as an exploratory `TP-` claim, which says whether the next batch is worth his time.

## Prior-evidence disclosure

The sample test of 2026-10-05/06 (outside the repo, spec amendment E1 to E14) labelled 150 listing ends; they are part of the frame and will be labelled again in the first batch, as every listing end is. `departure-reason-pilot`'s gold and `pilot` score precede every batch by design. No batch result exists.

## The shortlist and the metrics (req 9, req 10)

- **Shortlist** (`crosswalk.shortlist`, written once per run by `job.run_batch`): every disagreement (crosswalk rows 6 and 7 always), every `unresolved` (a `timeout` included), and `agreement_sample_size` = 30 agreements drawn under this file's seed (`20261019`), each with its stratum and the agreement sampling rate; items past `max_items` = 200 in acceptance order are `deferred`, counted, and carried to the next batch's selector. Both numbers are in the run's `config_json`.
- **Primary.** `yield`: the share of reviewed `disagreement` and `unresolved` items on which the rule was wrong (`decided_for = both_wrong`, or `decided_for = model` on a `disagreement` item; a `model` decision on an `unresolved` item means the filing does not say, which is not a rule error), `direction = greater`, **no threshold** (verdict `n/a`: ADR 0013 point 5 makes yield reported, not gating), with its one-sided Clopper–Pearson lower bound.
- **Secondary, reported.** `model_accuracy` and `rule_accuracy`, stratum-weighted over the batch (each reviewed item weighted by the inverse of its stratum's sampling rate: `disagreement` and `unresolved` 1.0, `agreement_sample` by `agreement_sample_size / n_agreements`), the agreement stratum's lower bounds reported separately and flagged `underpowered` under 20 reviewed agreements; `unresolved_share`; `n_reviewed`; `n_deferred`; `cost_usd`.

## Seeded error set (as registered with `departure-reason-pilot`)

The seeds are scored by `departure-reason-pilot` (`seeded_recall`); a batch shortlists them like any listing end. Recorded here, as req 10 asks of every file, with the same pre-fix readings. A seed whose Form 25 the frame does not hold is dropped and noted at registration. Each seed's **pre-fix rule answer** is the output of the pre-fix rule function at the fix's parent `code_version`, as traced on its issue; the crosswalk row that output gives is written into `config_json` with that commit at registration. The traced reading from each issue:

| Seed | Issue | Form 25 | Parent `code_version` | Pre-fix behaviour as traced on the issue | Pre-fix rule answer (crosswalk reading) |
|---|---|---|---|---|---|
| Valaris (Class A, `0000314808`) | #921 | 25-NSE filed 2020-09-04, effective 2020-09-14 | `f2b5261` | the footnote alias `VAL*` folded into `VAL` kept the Form-25-delisted span alive; the post-emergence common became a plain class of the old company, not a `<cik>@<date>` successor | the line the rule did not end, no successor: row 6 |
| Seagate 2021 (`0001137789`) | #943 | the Ireland redomicile, effective 2021-05-28 | `ca79641` | a repair dry run would cut the live STX line (1,714 bars): the redomicile and relisting filings read as an end | delisted, no relisting, no successor: row 4 or 5, by the Form 15 marker |
| MiMedx 2019 (`0001376339`) | #943 | Nasdaq delisting effective 2019-03-08 (relisted 2020-11) | `ca79641` | the long-gap relisting (#905's accepted cost) cut the 2020 relisting off the same security | delisted, no relisting: row 4 or 5, by the Form 15 marker |
| Sotheby's 2019 (`0000823094`) | #905 | 25-NSE effective 2019-10-13 | #847's parent | a debt cover page after the stopped line kept the delisted `BID` span alive | the line the rule did not end: row 6 |
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

## Parameters

```toml experiment
slug = "departure-reason-batches"
kind = "benchmark"
stage = 3
title = "Departure reason, labeling batches: the yield of the owner's shortlist review"
confirmatory = false
provenance = "model_historical"
touches_returns = false
claims = ["ER-14", "ER-15"]
hypothesis_ref = "B8"
seed = 20261019
splits = ["full"]

[dataset]
name = "departure-reason-frame"

[window]
start = 2016-01-01
end = 2026-12-31

[primary]
metric = "yield"
direction = "greater"
ci_level = 0.95
min_clusters = 30
inference = "Clopper-Pearson one-sided 95% lower bound over the reviewed disagreement and unresolved items"
secondary = ["model_accuracy", "rule_accuracy", "unresolved_share", "n_reviewed", "n_deferred", "cost_usd"]
comparison_set = "the pinned model's label against the master and resolver's rule answer at t, through the crosswalk; the owner decides each shortlisted item blind"

[multiplicity]
method = "none"

[budget]
runs = 6
configurations = 1
stop_rule = "no batch before departure-reason-pilot passes; a drift probe that fails closes the batch before its first call; the owner stops the batches when a batch's yield says the review is not worth his time (recorded in its TP- claim)"
expected_effect = "yield unknown before the first batch; the owner reviews at most max_items = 200 items per batch, the rest deferred to the next"
```

## Expected magnitudes and red flags

- A whole-population batch of about 11,150 listing ends at about $0.85 at list price (spec C1); far more is a packet-rule or estimate bug, and the mid-batch spend check stops it at the owner's ceilings.
- A shortlist dominated by row 6 (`listed` after the effective day): dual listings where one line was withdrawn; the owner's `rule` decisions measure it, and a sub-row is a crosswalk amendment.
- `rule_accuracy` near 1.0 with a high yield, or the reverse: the stratum weights or the attribution after the save are wrong.

## Retirement condition

The pilot's `TP-` claim not saying `pass` retires this registration unused. Afterwards, a vendor retirement of `jev-1.13.0` ends the stream (a new pinned id is a new registration, never a substitute), and the owner closes it when a batch's yield says the next is not worth his time.
