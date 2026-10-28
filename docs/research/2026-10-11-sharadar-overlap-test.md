# Research Report: the Sharadar overlap test (T144), verdicts only

**Plan task:** T144 (#1303 item 4, #1319) · **Date:** 2026-10-11 (merged 2026-10-27) · **Status:** COMPLETE · **Agent/model:** team sharadartest, claude-opus-5-5

> **Written 2026-10-11, merged 2026-10-27** (#2417), trimmed to verdicts. The draft was held back on Sharadar licence section 8 (evaluation conclusions not to be published) and then overtaken by the owner's "no vendor" decision of 2026-10-14 ([ADR 0022](../decisions/0022-no-vendor-security-master.md)), which recorded T144 as "not run"; no PR was opened. The owner's decision of 2026-10-27: keep the pass/fail verdicts here, with no Sharadar-derived figure.
>
> **Full record:** `~/tradepartner-probes/sharadar/` (local, not in git, listed in its `MANIFEST.txt`). The licence requires deleting it within 30 days of cancelling, so the verdicts below are the lasting record.

Read-only on a store copy (2026-10-11, after release restamp-1382a2); no portfolio return computed on any window.

## Answer
Under T144, any fail among (a) to (e) means T145 records **"no vendor"**. Four of five failed: **no vendor.**

| Test | Bar (from the T144 plan line) | Verdict |
|---|---|---|
| (a) identity crosswalk by CIK, one-to-one, 2019-12-31 and 2023-12-29 universes | at least 97% | FAIL |
| (b) 2016-18 delisted completeness (EDGAR Form 25-NSE population) | at least 90% | FAIL |
| (c) raw closes within 0.5% (2,000-row sample) | at least 99.5% | PASS |
| (d) splits and ticker changes agree | at least 98% | FAIL |
| (e) fundamentals datekey | never before the filing day; at least 95% within a day | FAIL |
| (f) 1998-2015 permatickers with bars per year | none (report only) | reported |

## Causes
- (a): multi-class issuers (no CUSIP in the store), a current-CIK-only vendor field, and a few store duplicate ids.
- (b): series that continue past the Form 25, series that end well before it, and filers with no CIK match; the store holds bars for few of these filers.
- (c): mostly vendor rounding of sub-dollar closes.
- (d): Alpaca spin-off factors stored as splits, some splits absent from the vendor, ratio and one-day differences.
- (e): mainly filings accepted after 17:30 ET carrying the same calendar date; the vendor date is EDGAR's filing date, not the acceptance time.

## Disconfirmation
Vendor fundamentals dates are EDGAR filing dates, not acceptance times (look-ahead if stamped at close of datekey); one CIK per security, no history; closes rounded; some splits missing. In its favour: it carries a series for most 2016-18 delisters, which the store largely lacks, and 18 pre-2016 years.
