# Research Report: the Sharadar overlap test (T144), method and record only

**Brief:** #2413 (data-foundation plan T144; pass bars in PR #1323 for #1319)  ·  **Date:** 2026-10-27  ·  **Status:** COMPLETE (the test ran; its results are held privately, see below)  ·  **Agent/model:** team sharadartest, claude-opus-5-5

## Answer
**Verdict:** not published here  ·  **Confidence:** n/a

The test ran on 2026-10-27 against every pre-set bar. **Its numbers and verdicts are not in this file.** Sharadar's Personal Use License, section 8, says that the conclusions of any test or evaluation of the data's "value, usability or fitness for purpose" may not be "published in any way in any document" without Sharadar's prior written approval. This repository is public. So the full report, with every count, the PASS/FAIL table and the disconfirmation items, sits in the owner's research library (`~/tradepartner-probes/sharadar/2026-10-27/2026-10-27-sharadar-overlap-test.md`), and the owner has it. This file records what was run and how, so the method can be audited without the results. The rule for this test is [ADR 0022's amendment of 2026-10-27](../decisions/0022-no-vendor-security-master.md#amendment-2026-10-27-2413-a-paid-sharadar-overlap-test-under-the-licence).

## What was tested, and against which bars
- **The bars are T144's, unchanged.** They were written into the plan line before any purchase (PR #1323, merged 2026-10-08: `git show 1245f8d8:docs/plans/data-foundation.md`, the T144 line). The owner confirmed them as written on 2026-10-11 (#1303, newest owner comment). These five bars: (a) the identity crosswalk, at least 97% one-to-one; (b) 2016 to 2018 delisted completeness, at least 90%; (c) raw closes, at least 99.5% within 0.5%; (d) actions, at least 98%; (e) the fundamentals `datekey` never before our acceptance and at least 95% within one day. Report (f), with no threshold: `permaticker`s with bars per year, 1998 to 2015. T144's rule: any fail among (a) to (e) means T145 records "no vendor."
- **The operational method was written on 2026-10-11, before the first vendor download**, and was not changed for this run (`~/tradepartner-probes/sharadar/METHOD-prereg.md`, owner's library). In short: (a) store `securities.cik` against the CIK in Sharadar TICKERS `secfilings`, never by ticker, restricted to SEP "Domestic Common Stock" categories, over `universe_as_of` at the 2019-12-31 and 2023-12-29 session closes; one-to-one means exactly one common `permaticker` whose price span contains the date, claimed by no other member. (b) First equity Form 25-NSE filers of 2016 to 2018 from the EDGAR full index; a pass is a mapped series ending within 5 XNYS sessions of our last bar, the Form 25 or its effective date. (c) A 2,000-row uniform sample (seed 144) of common-stock bars, plus every reused-ticker segment, our close against SEP `closeunadj`. (d) Every split and ticker change 2016 to 2026 against ACTIONS on date and ratio. (e) 50 issuers by rank, every ARQ/ARY `datekey` against the acceptance's filing day.
- **(e) was not re-run.** The plan bought on 2026-10-27 is Prices, which has no SF1 fundamentals table. The full report quotes the 2026-10-11 run's (e) figure instead, from the access the owner held then (below).
- **Added reports, gating nothing, labelled "added" in the full report:** a 50,000-row close sample (seed 2413); the C1 set of 1,301 CIKs that stopped filing in 2017 to 2019 (count reproduced); the funds SPY and MTUM against SFP (RSP and CSD are not in the store); dividend dates; whether SEP's OHLC is split-adjusted; bars per year 1998 to 2015 for SEP and SFP.

## How it ran
- **Vendor side:** the Sharadar direct API, one full-history bulk call each for `tickers`, `actions`, `stocks` (SEP) and `funds` (SFP), four calls in all. The files sit outside the repo, in `~/tradepartner-probes/sharadar/`, and are listed in its `MANIFEST.txt`. No vendor row is in this repository, a team directory or git.
- **Store side:** read-only DuckDB connections to the owner's store, each open under a minute, writing nothing. Bars are the latest revision per security and session. No signal, return or portfolio is computed on any window, and no trial is registered. The universes are read at each date's session close, from rows known by then.
- **Secret handling:** the probe reads `SHARADAR_API_KEY` from `.env` inside its own process, sends it only as the `x-api-key` header, never in a URL, and redacts it from any error text. Bulk calls take the redirect without following it, so the key never reaches the download host, and the pre-signed link is never printed. `calls.log` records only the time, the API path and the HTTP status.

## Disconfirmation
The full report has the disconfirmation items. They cover where the vendor is worse than Alpaca or EDGAR, and where this test is weak.

## Caveats & gaps
- **An earlier run exists.** On 2026-10-11 the owner bought a Sharadar plan (#1303: "the annual plan"), and a first T144 run used the same bars and method. That run's draft was never published, for the same section 8 reason, and ADR 0022 (2026-10-14) recorded T144 as "not run." Its files are still in the owner's library. Today's run is the second one. Neither run moved a bar.
- Two records disagree, and the owner settles which is right. #1303 records an annual plan bought on 2026-10-11 (deletion "on cancel only"). This task's brief records one month of Prices bought on 2026-10-27. The deletion rule in the ADR amendment applies to whichever subscription ends.

## UNVERIFIED items
- Whether Sharadar would give written approval to publish the results. Nobody has asked.

## Follow-up questions (not answered here)
- T145, whether to subscribe for a year: the owner decides from the private report.
- Whether to ask Sharadar for section 8 approval, so that the numbers can enter this file and `claims.toml`.

## Sources
- S1: Sharadar Personal Use License Terms, sections 4, 8 and 10 (read 2026-10-11, copy in the owner's library).
- S2: data-foundation plan T144 as merged in PR #1323 (commit 1245f8d8).
- S3: #1303, the owner's comment of 2026-10-11.
