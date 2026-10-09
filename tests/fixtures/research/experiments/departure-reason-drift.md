# Experiment: Departure reason, drift probe and the reversed-order pass

**Kind:** robustness  ·  **Stage:** 3  ·  **Author:** team labelcli (plan T124, #1348), for the owner  ·  **Date:** 2026-10-09

Merging this file does not register it. The owner runs `tradepartner experiment register docs/experiments/departure-reason-drift.md` with the other two (plan "Owner items" step 5).

## Rationale

ADR 0013 point 5 asks for "a fixed probe set with gold labels, re-sent at the start of every later run as a `robustness` run", so that a silent re-route or a drift of the vendor's model shows before a batch spends the owner's time. Spec amendment C5 sets it at its smallest, as the owner chose on 2026-10-06 (question 3 (a)):

- **Probe set.** 20 `dev` items of `departure-reason-gold`, drawn under this file's seed (`20261029`) from the baseline's answered items (`job._run_drift`).
- **Baseline.** Their inference records from the frozen configuration's `dev` run under `departure-reason-pilot`; that records file's SHA-256 is written in the drift run's result, so the baseline cannot change unnoticed.
- **Each probe.** `research label` on a frame batch first opens a run here (split `dev`), re-sends the 20 packets (each the same packet kind and passage hash as its baseline record; the comparison is per call) at the pinned id and option-set hash, and computes `flip_rate` against the baseline. A verdict other than `pass` closes the batch before its first call (`drift probe not passed`). `mean_abs_probability_shift` is reported.
- **The reversed-order pass.** Once, at the freeze, when a command can run it (none can yet, #1351): the 30 `dev` items with the option order reversed, recorded as a run of this registration, its `flip_rate` and its `unresolved` share under reversal quoted in the pilot's `TP-` claim. It is reported, not a gate (C5 drops the gating perturbation probe; E6 found the same choice on 20 of 20 under reversal).
- **Claims.** ER-14 and ER-15, as for the pilot. The reversed pass is an option-order check, which ER-15 (the binding of an option name to its rubric) does not grade.

## Prior-evidence disclosure

The sample test of 2026-10-05/06 (spec amendment E6, E8): the same request three times gave the same choice on 20 of 20 with a median probability shift of 0.00 and a maximum of 0.05; the option order reversed gave the same choice on 20 of 20 (18 of 20 on the weaker Form-25-fields-only passages of run 1); one model id on 308 of 308 calls. Those calls were on the excluded issuers, never on gold items.

## The bar

`flip_rate`, `direction = less`, `threshold = 0.10`, a point estimate at n = 20: one flip (0.05) passes and is listed; two or more (0.10 and up) fail. A baseline whose own repeat already fails the bar is a finding that stops the pilot, not a bar to lower (spec Risks).

## Seeded error set (as registered with `departure-reason-pilot`)

The seeds are scored by `departure-reason-pilot` (`seeded_recall`) and are not in the probe set unless the `dev` draw picked one. Recorded here, as req 10 asks of every file, with the same pre-fix readings. A seed whose Form 25 the frame does not hold is dropped and noted at registration. Each seed's **pre-fix rule answer** is the output of the pre-fix rule function at the fix's parent `code_version`, as traced on its issue; the crosswalk row that output gives is written into `config_json` with that commit at registration. **Open (#1351):** no command adds the seeds to the gold session yet and `seeded_recall` is not computed against these answers; and on the traced rows 4 or 5 a correct label is an agreement, so the `seeded_recall = 1.0` gate below waits on the owner's decision there. The traced reading from each issue:

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

**Window.** `[window] end = 2026-12-31` stands in for spec req 10's "to the freeze": the freeze date does not exist when the file is merged, and the registry needs a literal date. What keeps post-freeze filings out is the frame: it is built once at `t` before the freeze (the frame drops every listing end accepted after `t`), the freeze date is recorded on #963, and this registration binds only `departure-reason-gold`, whose rows all come from that frame.

## Parameters

```toml experiment
slug = "departure-reason-drift"
kind = "robustness"
stage = 3
title = "Departure reason, drift probe and the reversed-order pass"
confirmatory = false
provenance = "model_historical"
touches_returns = false
claims = ["ER-14", "ER-15"]
hypothesis_ref = "B8"
seed = 20261029
splits = ["dev"]

[dataset]
name = "departure-reason-gold"

[window]
start = 2016-01-01
end = 2026-12-31

[primary]
metric = "flip_rate"
direction = "less"
threshold = 0.10
ci_level = 0.95
min_clusters = 20
inference = "point estimate at n = 20 against the frozen dev run's records (no interval): one flip passes, two fail"
secondary = []
comparison_set = "each probe call's selected option against the baseline record of the same listing end, packet kind and passage hash"

[multiplicity]
method = "none"

[budget]
runs = 12
configurations = 1
stop_rule = "a probe that does not pass closes its batch before the first call (drift probe not passed); a vendor retirement of the pinned id ends the stream, and the next pinned id is a new registration"
expected_effect = "flip_rate 0.00 to 0.05 (E6: 0 of 40 repeats flipped, 0 of 20 under reversal on packet B)"
```

## Expected magnitudes and red flags

- Any flip on a packet whose passage hash differs from its baseline's: the packet rule changed after the freeze, which is a new configuration, not drift.
- A returned model id other than the pinned one stops the probe on that call (`model id mismatch`) before any flip is counted.

## Retirement condition

Retired with `departure-reason-batches`: when the pinned id is retired by the vendor or the owner closes the batches.
