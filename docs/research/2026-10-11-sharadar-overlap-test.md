# Research Report: the Sharadar overlap test (T144), counts and verdicts only

> **Written 2026-10-11, merged 2026-10-27** (#2417). The text below is the draft as the `sharadartest` team left it on 2026-10-11, unchanged. Why it was not merged then: the draft was held back on Sharadar's Personal Use License section 8 (evaluation conclusions may not be published without Sharadar's written approval; this repository is public), and the owner had not decided that. Three days later the owner decided "no vendor" on #1682 (2026-10-14, [ADR 0022](../decisions/0022-no-vendor-security-master.md)), and ADR 0022 recorded T144 as "not run", so the report no longer gated T145 and no PR was opened for it (no commit of it exists on any branch; the plan line for T144 stayed ticked "not run"). It is merged now on the owner's instruction of 2026-10-27, with its three claims, because the vendor evidence it holds (a delisted-name series for 743 of 779 2016-18 delisters against 47 with store bars) is what the C1c survivorship check ([c1c-filing-delay-delisted](../experiments/c1c-filing-delay-delisted.md)) builds on. The section 8 question is not settled by this merge: see the PR for #2417. Where the text below says "DRAFT, NOT PUBLISHED", read it as of 2026-10-11.


**Plan task:** T144 (#1303 item 4, #1319) · **Date:** 2026-10-11 · **Status:** COMPLETE · **Agent/model:** team sharadartest, claude-opus-5-5
**DRAFT, NOT PUBLISHED.** Sharadar's licence §8 restricts publishing evaluation conclusions without written approval; the owner decides before this file enters the public repo.

Vendor rows are not in this file. The full report, every list and the scripts are in the owner's local research library (`2026-10-11-sharadar-overlap-test.md`). Read-only on a store copy (2026-10-11 17:56 ET, after release restamp-1382a2); no portfolio return computed on any window.

## Answer
| Test | Measured | Bar | Verdict |
|---|---|---|---|
| (a) crosswalk one-to-one (CIK; 2019-12-31 and 2023-12-29 universes) | 1,947 / 2,010 = 96.87% | ≥ 97% | FAIL |
| (b) 2016-18 first equity Form 25-NSE filers (EDGAR population, 779) | 612 / 779 = 78.6% | ≥ 90% | FAIL |
| (c) raw closes within 0.5% (2,000-row sample, mapped rows) | 1,910 / 1,918 = 99.58% | ≥ 99.5% | PASS |
| (d) splits and ticker changes agree | 2,600 / 3,241 = 80.2% (common/SPAC, real changes: 94.5%) | ≥ 98% | FAIL |
| (e) fundamentals datekey | 39 / 3,411 before the filing day; 98.3% within one day | never before; ≥ 95% | FAIL |
| (f) 1998-2015 permatickers with bars | 5,531 to 8,836 per year; 54.6% to 83.7% flagged delisted | none | report |

Under T144, any fail among (a) to (e) means T145 records "no vendor".

## Causes (counts)
- (a): 37 multi-class one-to-many (no CUSIP in the store), 22 no match (current-CIK-only vendor field; Canadian category), 4 store duplicate ids.
- (b): of 167 fails, 79 series continue past the Form 25 (OTC tails), 29 end > 30 days before it, 23 within 30 days, 36 no CIK match. The store has bars for 47 of the 779 filers.
- (c): 4 of 8 mismatches are vendor rounding of sub-dollar closes; 1 crosswalk class mix-up; 3 unexplained.
- (d): 32 Alpaca spin-off factors stored as splits; about 20 splits absent from the vendor; 10 ratio and 7 one-day differences; store listing artefacts in ticker changes.
- (e): 34 after-17:30 acceptances dated the same calendar day, 3 calendar artefacts, 1 store-match artefact, 1 genuinely early date; datekey equals EDGAR's Date Filed in 3,384 / 3,411.

## Disconfirmation
Vendor fundamentals dates are EDGAR filing dates, not acceptance times (look-ahead at close-of-datekey stamping on about 1% of rows); one CIK per security, no history; closes rounded to 2-3 decimals; some splits missing. In its favour: a series for 743 / 779 2016-18 delisters against 47 with store bars, and 18 pre-2016 years.
