# Hypothesis: I2, one-year share issuance

**Family:** `issuance` (proposed root family; not engine-ready)  ·  **Author:** team codexissue, #2669  ·  **Date:** 2026-10-30  ·  **Status:** DRAFT for the owner; not registered

The [desk read](../research/2026-10-30-equity-issuance-desk-read.md) clears the prewritten size-screened line for `ShareIss1Y` but does not establish a TradePartner book. Promotion under [research-program §4](../ways-of-working/research-program.md#4-promotion-backlog--hypothesis-file) awaits a store feasibility count, verified portfolio direction, an issuance-family engine and merged plan task, and the owner's registration. No parameter below is a claim that the current registry can parse it.

## Economic rationale

Share count change between t−18 and t−6 months may proxy managers issuing stock when overpriced. The prior is **at most +0.07%/month gross**, the Chen–Welch luck-adjusted Tier-2 non-microcap post-2005 estimate for the broader anomaly class; after the +0.1087%/month liquid rebalance cost margin it is near or below zero. The CZ size-screened high quintile had +0.134%/month excess over its five-portfolio mean in 2009–25 and +0.260 in 2020–25; both are selected, gross results. The sign convention must be checked against `SignalDoc.csv` before any implementation: its `Sign = −1` and Q1's low-issuance proposal do not by themselves prove that CZ's high portfolio is the desired low-issuance long leg.

## Parameters — proposed frozen registration

- Signal: one-year growth of split-adjusted shares, latest cover/dei `shares_outstanding` facts available as of t−6 and t−18 months (`known_at` = filing acceptance), mapped to the listed security as of each read. No later fact or later-known split enters either point.
- Formation and cadence: monthly, at the last XNYS session's close; one-session delayed fills at the next session's open; hold the favorable top quintile under the verified sign convention, with an equal-weight comparison to the eligible universe. A short leg is research-only until borrow availability and cost are measured.
- Practice: earliest date with two valid lagged share observations and complete bar/split history, through the global development boundary **2023-12-29**. Historical exam of record: exactly **2024-01-01 through 2026-09-30**, as ADR 0016 point 8 allows for one frozen finalist. No further selection or parameter change after reading it; that period is already partly exposed by this desk read, so the exam can only reject.
- Universe: common listed US equities under ADR 0006's as-of membership and delisting rules. For a daily or week-end paper variant, the [research-program default](../ways-of-working/research-program.md#registration-defaults) freezes a **$200M** floor on median raw close × volume over the prior 20 sessions, with observations known by t; a monthly registration must explicitly choose its universe. Proposed here: adopt the same $200M floor for comparability, subject to the owner's approval. The CZ NYSE 20th-size-percentile screen is a different universe.
- Cost: ADR 0023 rung-1 arrival-relative, order-equal buy/sell mean on rebalance days in the registered band. Current liquid measurement **8.8 bp/side**; freeze base at 8.8, sensitivity at 0, 15.6 and 24.2 bp/side, plus **30 bp/year** short borrow if a short leg is permitted. Annual one-sided turnover 100% is a screening estimate, not a frozen engine input. Two-leg x2-margin cost line: **0.1087%/month** at base, **0.154%** at 15.6, **0.2113%** at 24.2.
- Trial budget: one one-value sweep registration and one practice run; at most one promoted finalist and one historical exam spend. Harsh rung 4 (double cost, one-session delay, open fills) and rung 5 placebo each count as trials in family N, including failed or bugged reruns under registry rules. No tuning on the shared historical exam.

## Expected magnitudes and red flags

Gross favorable long-leg excess prior 0 to +0.07%/month, net near or below zero. An apparent +3 pp/year net excess over SPY is a red flag to audit share splits, filing timing, sign, delisters, and large-name concentration. Compare to size- and date-matched peers as well as SPY and MTUM; do not use SPY alone to diagnose a small-stock effect.

## Power arithmetic

The fixed point-8 exam is 33 calendar months, **2.75 years**. At an assumed 8% annual tracking error and a generous +1 pp/year true excess, t ≈ (1/8) × √2.75 = **0.21**. t ≈ 2 would need about **9.65 pp/year**, far above the prior and the red-flag level. Even an 8.4%/year tracking error gives t ≈ 0.20 at +1 pp/year. This exam can **only reject** a finalist with a large negative or cost failure; it cannot confirm the expected small edge. Its months are partly seen through the CZ read, so the label must disclose that exposure on every report.

## Prior-evidence disclosure

The CZ files were read through **2025-12**, including 2024–25 inside the proposed exam, before this draft. All six ShareIss1Y cuts and the NetEquityFinance check are in the desk report. The 2026 portion has not been read in a TradePartner run here. Point 8 permits the shared window but does not make it virgin evidence.

## Retirement condition

Before registration, freeze the first audited full-window practice trial as the decision trial. Retire if its net excess CAGR over SPY is below zero **or** `dsr_excess < 0.5`; ADR 0023 rung 4 separately kills if net excess CAGR under harsh settings is below zero or `dsr_excess < 0.5`. A red-flagged run opens an audit and gives no verdict. A favorable practice result only permits the single historical exam; the paper book remains a separate tracking and operational gate.

## Open questions for the owner

Confirm the sign mapping and whether the first book is long-only or long/short; approve the monthly $200M universe choice; require the store feasibility counts and family engine plan before registration; and set the exact family trial cap in its first sweep.
