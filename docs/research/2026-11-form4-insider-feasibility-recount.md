# Form 4 insider purchases: T342 counts-only feasibility recount

**Brief:** #2679 (plan task T342, [research-registry plan](../plans/research-registry.md)) · **Date:** 2026-10-31 · **Status:** COMPLETE (counts and coverage only; no return read) · **Agent/model:** run by the orchestrator on the owner's word ("you do it"); note by team claudet342, Claude Opus 5.5

**Verdict: FAIL** (`FAIL (200M: 47 < 400 opportunistic events; 200M: zero delisted issuers in the treated pool)`, the script's own line). Under the plan's feasibility gate the B7 registered event study is **INSUFFICIENT under its own feasibility gate**. The frozen table says: "An infeasible clause yields INSUFFICIENT; it is not tuned after data are seen". No threshold, stratum or window was changed.

## Answer

The snapshot is complete under the T343 ratio rule: 0 of 32 quarters are below 0.8 × the median. The $200M stratum then fails two of the plan's FAIL clauses on its own:

1. It has at most **47** opportunistic events against the 400 floor. Since `n_eff <= n`, it cannot reach 400.
2. **No** Form 25 delisted security falls in its treated pool.

All names passes every gate on these counts: 2,882 events, 0.6% missing entry bars, and treated and neutral delister coverage of 100% and 93.6%. But the frozen kill line is conjunctive across strata ("n_eff ≥ 400 in **each** stratum"), and the plan says the recount "may not ... drop a stratum". So all names alone cannot carry the study. The plan's outcome on FAIL therefore applies. The study is recorded INSUFFICIENT by this note and by the B7 rows of the [hypothesis backlog](hypothesis-backlog.md), and T332 to T341 and T344 are struck, not built.

Confidence: **high** that the label is FAIL. The floor clause holds under every interpretation choice the script made and alone carries the FAIL. The treated-delister clause holds under choices 1 to 7 and 10; under choice 8 and #2720 only the floor decides (see [Robustness](#why-the-fail-is-robust)). The 47 is an upper bound.

## What ran

| Item | Value |
|---|---|
| Script | `~/tradepartner-probes/t342/recount.py`, sha256 `9dcb98de00428c2e6c22c47214a1222238186d16ec4868abaee5c746fef13c15` (printed on its first output line; re-hashed for this note) |
| Its notes | `~/tradepartner-probes/t342/README.md`: run instructions, an output-to-T342 map, and ten numbered interpretation choices |
| Output | `recount-diag.txt` sha256 `ae59ecdd2c0e659d0e26a117e1c0a244a3fd2d420b857ddfd4062e456d229347`; `recount-diag.json` sha256 `58337a26f3573d467ae8769f860ad147c2fc6c9f3bb5735778b677aa9eadf114` (same directory) |
| Store | A byte copy of the owner's live store taken 2026-10-31 21:18 New York (file mtime 2026-11-01T01:18:03Z, 2,279,878,656 bytes), opened `read_only=True`. The copy was deleted after the run |
| When | 2026-10-31, about 21:20 New York; runtime 44.7 s |
| Read | Counts and coverage only. From `prices_daily`, only `security_id`, `session`, `close`, `volume` and `known_at`. No return, adjusted bar, open/high/low or shares fact was read |
| Pins | The frozen pre-registration table ([desk read](2026-10-31-form4-insider-purchases-desk-read.md#frozen-pre-registration)), plus the owner's [T343 answers](https://github.com/josejuarez96/tradepartner/pull/2680#issuecomment-6551343398) (ratio, A1, A3, A4, A6, A7). The script has no tunable threshold: they are constants copied from those two records |
| Cutoff | No read after close(2023-12-29) = 2023-12-29T21:00:00Z. Eligible acceptances [2020-01-01, 2023-12-01) UTC; history from 2017-01-01 |

**Data state.** The copy was taken after the backfill's 2026-10-31 20:11 run and the `roles-2691` release. It was taken **before** the [#2720](https://github.com/josejuarez96/tradepartner/issues/2720) multi-owner roles correction and the [#2708](https://github.com/josejuarez96/tradepartner/issues/2708) security-title repair. The script's README asks for #2720 to land first. The plan line and the B7 backlog row place #2720 before the study (T332 to T341), not before T342. Why neither can change the label is under [Robustness](#why-the-fail-is-robust). #2708 touches only `security_title`, and the script never reads that column.

## Snapshot completeness (T343 ratio, blocking)

**COMPLETE: 0 of 32 quarters (2016Q1 to 2023Q4) below 0.8 × median; no quarter with zero accessions.** The lowest ratios are 2023Q4 0.92 (its last two days are cut by the read boundary, choice 6), 2019Q4 0.92, and 2022Q4 and 2023Q3 0.93. Every year from 2017 to 2023 is fully observed (A1). 2016 is not, by rule, since A1 requires 2017 or later. The first run's nearly empty quarters (2018Q4, 2019Q1, 2019Q3 to Q4, 2020Q1, 2020Q3, 2021Q3) are now at 0.92 (2019Q4) to 1.11.

Qualifying-row series (non-blocking): rows ratio from 0.70 (2019Q4) to 1.69 (2018Q4). The per-quarter table is in `recount-diag.txt` (`[ratio]` lines).

## Per stratum

The registered window is acceptances 2020-01-01 to 2023-11-30 and exits by close(2023-12-29). It holds 4,703 coalesced opportunistic issuer × entry-session events, 3,662 of them mapped to a US common security (A3). Other kinds: 1,143 routine, 123 mixed, 33,134 unclassifiable. 490 events were moved by the 15-minute buffer.

| Gate | $200M | All names |
|---|---|---|
| Opportunistic, otherwise eligible | 47 | 2,899 |
| **Opportunistic with entry bar: n, upper bound (400 floor)** | **47 (21 issuers, 43 sessions): FAILS** | 2,882 (471 issuers, 834 sessions): holds |
| Entry-bar gate, `no_entry_bar` / otherwise eligible (10%) | 0 / 47 = 0.0%: met | 17 / 2,899 = 0.6%: met |
| **Treated delister coverage, A6 (90%)** | **0 / 0 in span; 0 delisted securities: FAIL clause** | 2 / 2 = 100.0% (2 securities) |
| Treated, reported only: effective-date / delisted-any-time | 0/0, 0/0 | 0/4 = 0.0%, 199/227 = 87.7% |
| Neutral delister coverage, A6 (90%) | 27 / 27 = 100.0% (16 securities) | 8,538 / 9,126 = 93.6% (592 securities) |
| Neutral, reported only: effective-date / delisted-any-time | 0/16 = 0.0%, 435/435 = 100.0% | 115/6,096 = 1.9%, 260,349/269,717 = 96.5% |
| Classifiable share (otherwise eligible events, any kind) | 51 / 1,146 = 4.5% | 3,757 / 28,844 = 13.0% |
| A4: neutral rows before / excluded; pool per session after (median, min) | 13,555 / 950; 289, 229 | 3,272,053 / 306,197; 3,600.5, 2,392 |
| Alt, treated = opportunistic + routine: treated / neutral delister | 0/0; 30/30 = 100.0% | 5/5 = 100.0%; 9,058/9,679 = 93.6% |

At vote level, 10,353 of 55,229 window votes (18.7%) are classifiable. Of the unclassifiable votes, 44,525 lack a qualifying trade in a prior year (the purchases-only history the desk read discloses as a CMP departure), and 351 need a year that is not fully observed (all with transaction dates in 2019 or earlier). Other checks, all at their expected values:

- A3 classification ties: 0.
- Live successor chains at the cut: 0.
- A7 late bars: 0 in the read. No treated or neutral pair has one in its 20- or 120-session window.
- The alt verdict carries the same label.

The effective-date reading is near 0 because `effective_on` is always the filing date + 10 days in this store (T343, known data limits). It is reported only.

**Against the first, partial run** (plan amendment; its counts were not necessarily the same quantities as these, so this is a rough comparison): the plan reports 16 opportunistic events at $200M and 1,830 in all names, about 35% of all-names events without an entry bar, and 8% to 27% neutral in-span delister coverage (stratum not stated). This run gives n = 47 and 2,882, 0.6% and 93.6% (all names). Two changes are plausible causes, the second backfill run and T343's A3 (unmapped issuers leave the entry-bar denominator), but their shares were not measured. Only the $200M stratum still fails.

## Why the FAIL is robust

Each $200M clause is enough for FAIL by itself. The floor clause holds under every choice in the script's README and alone carries the FAIL; the treated-delister clause holds under choices 1 to 7 and 10, and under choice 8 only the floor decides. Where a cell is reasoning rather than a counted bound, it says so:

| # | Choice | Can it move the $200M floor (47 vs 400)? | The treated-delister clause? |
|---|---|---|---|
| 1 | "Otherwise eligible" leaves out the runner's `no_mcap` and `no_beta` drops (they need shares facts and adjusted bars) | Only down: applying them can remove events, never add one. **The true n is at most 47** | Removing events cannot add a delister |
| 2 | Treated pool for coverage = opportunistic (alt adds routine) | No (the floor is opportunistic by the frozen table) | No: alt is also 0 / 0 |
| 3 | Neutral pool sessions = entry sessions of eligible opportunistic events | No | No (it touches the neutral pool only) |
| 4 | "Between entry and scheduled exit" includes both ends | No | No: the delisted-any-time reading, which takes any last listed session at or after entry, is also 0 / 0. None of the 47 events is on an A6 delister with a last listed session at or after entry |
| 5 | Bar existence on raw traded bars, not adjusted | Not upward. The price and liquidity filters use raw close and volume as A3 pins. `no_entry_bar` is 0 of 47, so n already equals the eligible count and cannot exceed it | No |
| 6 | Ratio counts use form `4` only; 2023Q4 cut at the boundary | No. Another ratio reading can only add an "incomplete snapshot" clause, which is another FAIL | No |
| 7 | History from 2017-01-01; the window filter is on acceptance, before coalescing | No. History from 2017-01-01 and acceptances 2020-01-01 to 2023-11-30 are the frozen values applied as written, and A1 already rules out years before 2017 as observed | No |
| 8 | `securities` CIK map as of the window-end cut; classification as of each entry cutoff | No. Classification half (counted): the script's diagnostic gives only 257 `no_common_security` events, of any kind and any liquidity, that would map under the window-end classification; 47 + 257 = 304 < 400 even if every one were opportunistic, at least $200M and had an entry bar. CIK-map half (reasoning): the window-end cut is the latest read the boundary allows, so an earlier map (as of each entry cutoff) would know the same or fewer securities | Not shown robust: a remapped event could sit on a delister. Only the floor decides here |
| 9 | A3's class choice uses the bar on E−1 | No for the class choice: the rule already picks the most liquid common class, so another class cannot raise the dollar volume. Reasoning, not a bound: a different "prior raw bar" (the last bar before E rather than E−1) could admit a security with no bar on E−1; at a $200M median dollar volume such gaps are implausible at the 353-event scale | Reasoning only, as for the floor |
| 10 | The Form 4 tables are read by direct SQL, not the gated `sec_*_as_of` readers | No. The schema enforces `known_at = accepted_at`, and the script cuts on `accepted_at <= close(2023-12-29)`. The gated readers return the same rows | No |

Choices 1 and 5 can only lower n, choice 8's classification half has a counted ceiling of 304, and choices 8 (CIK map) and 9 are bounded by reasoning. No choice can lift the $200M stratum to 400.

**#2720, multi-owner roles (reasoning, not a measurement).** The correction re-pins officer and director flags among the reporting owners of stored multi-owner filings. The orchestrator gives about 46k such filings in the store, a figure not re-counted here. #2720 estimates 50 to 60 mispinned accessions among about 9,820 multi-owner accessions from 2022q4 to 2025q1, and fewer elsewhere. Two confirmed rows are wrong.

A role fix changes which owner's lines qualify and so which (filer, issuer) votes and histories exist. It cannot change an event's issuer, acceptance, price, dollar volume or bars. At $200M, 1,146 events of any kind are otherwise eligible and only 51 of them are classifiable (47 opportunistic, 4 routine). To reach 400, the fix would have to turn 353 of the 1,095 unclassifiable ones opportunistic (or create new eligible events) by giving their insiders three prior years of qualifying purchases, or a qualifying role, that they lack today. One corrected accession can serve as history for several later votes of the same (filer, issuer) pair, but even so this needs on the order of a hundred or more corrected accessions concentrated in $200M names, against the issue's estimate of tens. A new or relabelled event could sit on a security that later delists, so #2720 could in principle add a treated delister; for #2720, as for choice 8, only the floor decides.

The weakest link is that estimate. #2720's own step 1 counts the corrected accessions and will confirm or refute it. #2708 changes only `security_title`, which nothing here reads.

## Outcome applied (plan amendment, "If T342 is FAIL")

- **The study is INSUFFICIENT under its own feasibility gate.** This note is the result. The B7 summary row, the B7 item and the B7 ladder-ledger row in the [hypothesis backlog](hypothesis-backlog.md) are updated in the same PR (class B).
- **T332 to T341 and T344 are struck, not built** (plan amendment). This PR does not edit the plan file: the T342 line names only this note and the B7 row. Striking the lines is an owner item below.
- **Family N under research-registry req 9.** The frozen table says "Count **N +2** ... even if a run fails or is refused". No run was opened, so N has not moved. The plan asks this note to state whether the owner charges the +2. **He has not said yet; it is open question 1, to be answered on this PR (class B, so it waits for his word anyway) and recorded here and in the B7 rows before merge.**
- The search-limit tally already counts B7 as candidate 3, with its registered event study counted with it. That does not change.

## Open questions for the owner

1. **N +2:** is the +2 for the B7 registered test charged to family N now that the study is INSUFFICIENT at its feasibility gate before any run was opened? The frozen table counts it "even if a run fails or is refused". This note records neither choice.
2. **Ledger decision value:** the plan directs "INSUFFICIENT" but not a ledger `decision` value. The B7 row now reads "registered study INSUFFICIENT at its feasibility gate". Should it read `retired`, `parked` or `deferred: <reason>` (for example, re-entry only with a new registration)?
3. **Striking T332 to T341 and T344** in `docs/plans/research-registry.md`: in a follow-up PR (class B), or by the fold?
4. **L5:** should the verdict also get a `TP-` claim in [claims.toml](claims.toml)? Ladder rule L5 records ladder verdicts that way, and this PR does not add one.
5. **Committed queries:** the plan asks for the method's queries in this note's appendix; the appendix gives gate definitions in words. Commit `recount.py` (sha256 above) or its SQL into the repo, or waive it?

## Caveats & gaps

- The run preceded #2720. The script README's step 1 asked for #2720 first, and the robustness argument for it is reasoning, not a recount.
- n is an upper bound: the `no_mcap` and `no_beta` drops were not applied.
- The all-names treated delister set is two securities, so its 100% coverage is thin and reported only.
- The method is committed below as gate definitions, not as queries. The plan's T342 line asks for the queries in an appendix; the script that ran lives outside the repo (`~/tradepartner-probes/t342/`), pinned by its sha256, so the FAIL cannot be re-run from the repo alone. Committing the script or its SQL, or waiving that, is open question 5.
- Voluntary delistings are absent: the store holds exchange-filed 25-NSE forms only (T343, known data limits).

## UNVERIFIED items

- The "about 46k" stored multi-owner filings (orchestrator's figure).
- #2720's estimate of 50 to 60 mispinned accessions.
- That adjusted and raw traded session sets are equal (choice 5; not checked, because checking reads adjusted bars).

## Sources

- [Desk read and frozen pre-registration](2026-10-31-form4-insider-purchases-desk-read.md)
- [Research-registry plan](../plans/research-registry.md): the 2026-10-31 amendment, its feasibility gate and line T342
- [T343 owner answers](https://github.com/josejuarez96/tradepartner/pull/2680#issuecomment-6551343398)
- [#2720](https://github.com/josejuarez96/tradepartner/issues/2720) and [#2708](https://github.com/josejuarez96/tradepartner/issues/2708)
- [Hypothesis backlog B7](hypothesis-backlog.md#b7-insider-purchases-monthly)
- The output files and script named under [What ran](#what-ran)

## Appendix: method (gate definitions)

These are the script's definitions in words, not its code. Every constant comes from the frozen table or T343.

**Calendar and clocks.** XNYS sessions come from `tradepartner.calendar`. A filing accepted at instant *a* enters at the first session E with *a* ≤ open(E) − 15 min, equality included. It is *buffered* when it would have entered earlier without the buffer. The horizon is E to E + 19 (entry counted as 1). An event is in the window if E is on or after 2020-01-02 and E + 19 is on or before 2023-12-29.

**Snapshot ratio (blocking).** For each New York acceptance quarter from 2016Q1 to 2023Q4, the script counts distinct form-`4` accessions in `sec_filing_events` accepted by the cutoff. A quarter passes if its count is at least 0.8 × the median of the same quarter in the two years either side, staying inside 2016 to 2023. Any failing quarter stops the run with FAIL (incomplete snapshot). A year is fully observed (A1) if it is 2017 or later and all four of its quarters pass. The qualifying-row series uses the median of the two quarters on each side and never blocks.

**Qualifying lines.** These are rows of `sec_form4_transactions`, which holds non-derivative transactions only, that meet all of the following:
- code `P`, acquired/disposed `A`;
- shares > 0, price > 0;
- officer or director;
- accession of form `4` (so no 4/A);
- accepted from 2017-01-01 UTC through the cutoff.

CIKs are normalised to 10 digits.

**Votes and labels.** There is one vote per (accession, issuer, filer). Its month and year come from the earliest `transaction_date` among its lines. Only votes accepted in [2020-01-01, 2023-12-01) UTC are labelled. A vote's history is that pair's qualifying lines in other accessions accepted strictly earlier. The labels:
- **unclassifiable** if any of the three prior calendar years is not fully observed, or has no qualifying line;
- **routine** if the vote's month appears in all three prior years;
- **opportunistic** otherwise.

**Events.** Votes coalesce to one issuer × entry-session event. It is unclassifiable if any vote is, opportunistic if all are, routine if all are, and mixed otherwise.

**Mapping (A3).** The issuer CIK maps to `securities` (as of the cutoff), restricted to securities whose `classifications.security_type` is `common` as of open(E) − 15 min. An issuer with no such security is `no_common_security` and is not otherwise eligible. Among several common classes, the event takes the one with a prior bar and the highest 20-session median raw close × volume; ties go to the lowest `security_id`.

**Bars.** The read is the latest revision of each (security, session) known by the cutoff, traded bars only (volume > 0), successor-chain aware: the same cut as `prices_as_of(traded_only=True)`, projected to five columns.

**Otherwise eligible.** In the window, mapped, and with all of the following:
- the prior-session raw close is at least 1;
- at least 10 bars in sessions E−20 to E−1;
- the 20-session median close × volume is at least the stratum floor (200,000,000 or 0).

**n** counts the otherwise eligible opportunistic events that also have a bar on E.

**Entry-bar gate.** (otherwise eligible − n) / otherwise eligible, against at most 10%.

**Delisters (A6).** For each security, the script takes the earliest Form 25 or 25-NSE accepted by the cutoff, using a /A only when there is no original. Its last listed session L is the session before the filing session (the New York acceptance date if a session, else the next one). Securities with any bar after `effective_on` are excluded.

**Treated coverage.** Among otherwise eligible opportunistic events on a delister with E ≤ L ≤ E + 19, an event is covered if it has a bar on every session from E through L. The gate is coverage of at least 90%. Zero such securities is a FAIL clause. The effective-date reading (L from `effective_on`) and the delisted-any-time reading (any L ≥ E, covered through min(L, E + 19)) are reported only.

**Neutral pool and A4.** The sessions are the stratum's otherwise eligible opportunistic entry sessions. Each common security on those sessions gets a row if it passes the same price, bar-count and dollar-volume filters. A row is excluded if its issuer has any qualifying line with close(E−21) < *a* ≤ open(E) − 15 min, which also removes the treated issuer's own event. Neutral coverage applies the treated rule to the remaining rows on delisters.

**Verdict.**
- **FAIL:** an incomplete snapshot, or in either stratum n < 400, or zero in-span delisted securities in the treated or neutral pool.
- **PASS-CLEAN:** not FAIL, and in both strata the entry-bar share is at most 10% and treated and neutral coverage are each at least 90%.
- **PASS-SURVIVOR:** neither of the above.
- A7 failures (pre-entry bars stamped after open(E) − 15 min) would mark the verdict "not final"; there were none.
