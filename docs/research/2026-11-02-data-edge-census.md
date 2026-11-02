# Research Report: the data-edge census (idea-pipeline pilot, source B)

**Brief:** #2908 (the [idea-pipeline spec](../specs/idea-pipeline.md), #2896, requirement 2 source B, requirement 7, work item 3)  ·  **Date:** 2026-11-02  ·  **Status:** COMPLETE (counts and dates only; no return read)  ·  **Agent/model:** team pilotb, Claude Opus 5.5

## Answer
**Verdict:** n/a (a census, not a test of a claim)  ·  **Confidence:** high on the counts, low on every effect size the cards quote (all UNVERIFIED or from the register)

The store holds two fields that most retail researchers lack, and both were already mined here: **Form 4 rows with exact acceptance times** (2016 to 2026-09, every row stamped) and **first-vintage statement facts stamped at the filing's acceptance** (eight facts, full from 2011). B7, C1, I1, CBOperProf and XFIN used them, and none survived. The fields that could carry new mechanisms are present only as **code or parse-time counters, not rows**: 8-K items (`filing_events`, 0 rows), the all-forms filing index (not seeded in this copy), later statement vintages (counted as `statement_restated`, never stored), 13D stakes (0 rows) and deal terms (not held). Corporate actions come from the broker's feed with no declaration date, so they give no timing edge.

The pass reviewed 33 field × event pairs and wrote 5 cards ([idea-cards.md](idea-cards.md#source-b-own-data-pass-of-2026-11-02-2908)): **0 `admit`**, 3 `blocked` on a data field, 2 `rejected: question 3`, and 28 one-line rejects. All five can be killed without a return read. The cheapest unblock is **T164e's real-store run** (owner, size S), which fills `filing_events` and also feeds candidate 1.

## What ran

| Item | Value |
|---|---|
| Store | The read-only copy `scratchpad/replay/store-20261102.duckdb` (schema 41, taken 2026-11-02 morning), opened `read_only=True`. No `data/*.duckdb` and no `.env` was opened |
| Scripts | Six counts-only scripts in the session scratchpad (`pilotb/census1.py` to `census6.py`); not committed, since they are counts this note reproduces in full |
| Read | Row counts, distinct counts, dates, `known_at` and `accepted_at` stamps, ingest run messages. From `prices_daily` only `close × volume` levels (a 20-session median, for liquidity bands) and bar existence. **No price change, return, adjusted bar, or open/high/low was read** |
| Cutoffs | Field census: `known_at` before 2026-10-01 for every figure (the first draft quoted unfiltered totals for six small tables and `statement_facts`; corrected after the quant-auditor's review). Event counts for power: acceptances before close(2023-12-29). Exception noted in place: one ingest counter from the 2026-10-06 rebuild run |

## The census

"Edge" is against a retail researcher with free EDGAR access and a bar vendor. PIT = point-in-time quality.

| Field (table) | PIT quality | Coverage by year | Known gaps | Edge |
|---|---|---|---|---|
| **Form 4 index rows** (`sec_filing_events`) | `known_at = accepted_at` on 100% of 2,085,950 rows (forms 4 and 4/A only); the ingest flags 268 to 338 clock-suspect stamps | 2016 to 2026-09: 183k to 218k rows a year, 5,050 to 5,850 issuers a year | Only forms 4 and 4/A; no Form 3, 5 or 144 | Moderate: aggregators show filing times too, but not a full PIT history |
| **Form 4 transactions** (`sec_form4_transactions`) | `known_at` = acceptance; `transaction_date` kept apart; 94% from SEC bulk insider datasets, 6% from XML | 3.69M rows, 1.60M accessions; 144k to 164k accessions a year 2016-2025; codes 2016-2023: S 854k, A 509k, P 393k, F 370k, M 360k | Non-derivative table only; no footnotes; no 10b5-1 flag (exists from 2023-04); about 186 accessions missing in 2016-2023 (B7 recount); lag from trade to acceptance median 2 days, over 4 days for 13% of P and 40% of G rows | Moderate |
| **Officer/director code-P purchase issuer-days by band** (derived) | as above; band = 20-session median close × volume before the acceptance date | 2016-2023: ≥ $200M 2,224 (418 issuers); $50-200M 5,160 (972); < $50M 52,706 (3,909); no bar 18,716 (2,332). 10%-owner-only: 148 / 496 / 7,638 / 5,624 | "No bar": issuers with no bar in the store (8,552 of the 18,716 fall in 2020-2023; why they lack bars was not measured, UNVERIFIED) | Feeds IC-B1, IC-B5 |
| **First-vintage statement facts** (`statement_facts`) | `known_at` = the filing's acceptance (time of day present: 0.0014% at exact midnight); exactly one accession per (CIK, fact, period): first vintage by design | 2,372,197 rows (`known_at` before 2026-10-01), 15,159 CIKs, all mapped to a security. Accessions by `known_at` year: 10-K 5,500 to 6,500 and 10-Q 16,900 to 19,700 a year 2016-2023; sparse 2009-2010, full from 2011 | Eight facts only (revenue, gross profit, cost of revenue, total assets, operating cash flow, three bank lines); no net income, equity, debt or current items until #2826 (T365); gross profit absent for about 37% of revenue filers | Real: a first-vintage PIT panel is rare outside paid vendors |
| **Periodic filing acceptance times** (via `statement_facts.known_at`) | as above, but only for filings that carry one of the eight facts | 2016-2023, non-amended: 10-Q pre-open 26,283, session 59,998, 16:00-17:30 51,782, after 17:30 8,594; 10-K 7,411 / 19,258 / 17,327 / 3,920. After-16:00 share: Monday 39%, Tuesday to Thursday 44 to 46%, Friday 35%. Period end to acceptance: 10-Q median 39 days, 10-K 70 | A filing with none of the eight facts has no stamp in the store | Feeds rejects 1 to 5 |
| **Later statement vintages** (parse-only) | The ingest reads every vintage from the SEC company-facts payload, keeps the first, and counts a later differing value as `statement_restated` | 253,797 differing later values against 2,134,064 first-vintage keys at the 2026-10-06 rebuild (all years; this run read filings to 2026-10-06, a few days past the census cut); not countable by year | Not stored; includes recasts (discontinued operations, segments, the 2018-2019 ASC 606 and 842 transitions) that are not errors | Real if stored: restated databases overwrite the event. Feeds IC-B2 |
| **Cover-page shares outstanding** (`facts`) | `known_at` = acceptance; `as_of_date` is the cover date | 372,384 rows, 14,111 securities, 2001 on; 21k to 25k rows a year on 5,600 to 6,600 securities 2016-2023 | Class split by `class_member` | Low |
| **All-forms filing index** (`filing_index`, T318) | Specified with acceptance times for every form | **Not in this copy**: the tables are absent and T324's seed is an owner step | Until then 8-K, DEFM14A, SC TO-T, 425, NT 10-K, CORRESP, S-1 have no stamp | Real once seeded. Feeds IC-B3 |
| **8-K items** (`filing_events`) | Specified: `known_at` = acceptance | **0 rows**: code merged (PR #1459), `edgar.filing_events_enabled` false, T164e's real-store run not made | Everything | Real once run (TP-1: half of 2.02s accepted 16:00 to 17:30). Feeds IC-B4 |
| **SC 13D** | Adapter code present | **0 rows** | Everything | C3 open elsewhere |
| **Delistings, Form 25** (`delistings`) | `known_at` = filing; `effective_on` = `filed_at` + 10 days on every row | 3,111 rows 2006 to 2026; by effective year 2016: 20, 2017: 19, 2018: 14, 2019: 78, 2020: 225, 2021: 351, 2022: 434, 2023: 624. Common by band at filing, 2019-2023: ≥ $200M 45, $50-200M 128 | Thin before 2019; no reason for the delisting; no bars after it | Low (public); the timing rule is source C's |
| **Corporate actions** (`corporate_actions`, broker feed) | `announced_at` empty on all 107,110 rows; `known_at` a median one day before the ex-date for dividends and splits, about one day after for spin-offs | dividends 103,919, splits 3,115 (2016 on), spin-offs 76 (2020-11 on) | No declaration date, so no timing edge | None |
| **Mergers** (`mergers`, broker feed) | at completion | 35 rows known before 2026-10-01, effective 2025-08 to 2026-09 (24 cash, 8 stock, 3 mixed) | No announcement, no failures, no history | None yet |
| **Symbol changes, listings, classifications, securities** | `known_at` per row | symbol changes 1,615 (2020-06 on); listings 16,906; classifications 102,172 (SIC 6770 SPACs 4,263 rows); securities 63,462 (all `known_at` before 2026-10-01) | | Low |
| **Daily bars** (`prices_daily`) | vendor SIP bars | 13.58M rows to 2026-09-30, 8,836 securities. Common names by band (an inline counts query of this pass, same band rule, bars on or before each date) on 2020-08-31 / 2021-12-31 / 2023-12-29: ≥ $200M 238 / 362 / 319; $50-200M 516 / 584 / 546; < $50M 3,354 / 3,914 / 3,753 | Delisters thin before 2019 (store census) | None (everyone has bars) |
| **T-bill rate** (`rates`, DTB3) | vintage and `known_at` per row | 2016-01 to 2026-09 (2,685 observations known before 2026-10-01) | | None |
| **Paper fills** | | Not an idea source (spec requirement 8): the cost figure only | | |

**What the band counts mean.** "≥ $200M" is 20-session median dollar volume, not market capitalisation: about 240 to 360 common names. That is narrower than the ADR 0006 top 1,000, and it is why every event mechanism in the store runs short of events at the default band (B7: 47; IC-B3: at most 45 completed deals; IC-B5: 35 issuers).

## The cards

| Card | Mechanism | Who pays | Verdict | Held capabilities suffice |
|---|---|---|---|---|
| IC-B1 | Officer and director purchases at $50-200M | Sellers and liquidity providers trading against insiders' information | `rejected: question 3` (the all-purchase gross is 0.74× break-even at the cheapest measured cost) | no (band cost unmeasured), but measuring it cannot admit the card |
| IC-B2 | Stealth comparative revisions | Holders of headline or restated data who never see the revision | `blocked: data field (later vintages)` | no |
| IC-B3 | Cash-deal merger arbitrage | Target holders who sell to exit; we carry the deal-break risk (a premium) | `blocked: data field (deal filings and offer terms)` | no |
| IC-B4 | Negative non-earnings 8-K items at low-attention times | Inattentive holders who react late | `blocked: data field (8-K items)`; the trade also needs a short paper book | no |
| IC-B5 | 10%-owner purchases | (weak: unaffiliated holders are the least informed insiders) | `rejected: question 3` | yes |

## Evidence
| Claim | Source | Tier | Key figure (quoted) | OOS / post-pub? | Net of costs? |
|---|---|---|---|---|---|
| Store counts above | This census, store copy 2026-11-02 | Own result | as tabled | n/a | n/a |
| Opportunistic insider purchases, monthly | G2-B (register) | MIXED | CMP VW 0.72%/month, pre-2008, gross | No | No |
| Insider reaction front-loaded | G2-5, G2-6 (register) | UNGRADED | 70-80% before disclosure | Partly | No |
| 2.02 stamps and lags | TP-1 (register) | UNGRADED | half accepted 16:00-17:30; 10-Q a median 1 day after the 2.02 | n/a | n/a |
| Merger-arbitrage premium | Mitchell and Pulvino, JF 2001; Baker and Savasoglu, JFE 2002; Jetley and Ji, FAJ 2010 | Tier 1, from memory, UNVERIFIED | about 4%/yr alpha 1963-1998; spreads shrank after 2002 | Partly | Partly |
| Friday inattention | DellaVigna and Pollet, JF 2009; Michaely, Rubin and Vedrashko 2016 | Tier 1, from memory, UNVERIFIED | 15% lower immediate, 70% higher delayed response (earnings); selection explains part | No | No |
| Stealth restatements | Files, Swanson and Tse, TAR 2009; Choudhary, Merkley and Schipper, JAR 2021 | Tier 1, from memory, UNVERIFIED | smaller announcement reactions; revisions predict later restatements | No | No |
| Large shareholders' trades least informative | Seyhun, JFE 1986 | Tier 1, from memory, UNVERIFIED | ranking only | No | No |

## Disconfirmation
- **Against IC-B1:** G2-5 and G2-6 (the reaction is front-loaded and mostly gone before disclosure), and the paper half-spread of +46 bp at $50-200M. The card fails break-even at the cheapest cost measured. G2-A is NOT SUPPORTED for liquid names on a days horizon.
- **Against IC-B3:** Jetley and Ji (2010) on shrinking spreads, and the store's own counts: at most 45 completed deals in band in 2019-2023.
- **Against IC-B4:** Michaely, Rubin and Vedrashko (2016): much of the Friday effect is which firms announce on Fridays. The card's matched Monday-to-Thursday comparison, and a volume test that needs no return, are meant to separate the two.
- **Against IC-B2:** most counted revisions are recasts, not errors, so the signal may be mostly noise. Only a stored field can show the share.
- **Against the source as a whole:** EDGAR is public. The spec says so, and the census confirms it: the two edges we hold were mined and failed, and the rest are not yet rows.

## Caveats & gaps
- The census reads one store copy. A field the owner's store holds but this copy lacks (the T318 tables after a T324 seed) is reported as not held.
- Event counts by band use a 20-session median of raw close × volume per security. A CIK with several common classes takes its most liquid class. Same-issuer clustering is not removed, so these are raw issuer-days, not effective events.
- The Form 25 band counts are an upper bound for completed deals: they include every delisting reason and miss failed deals.
- The `statement_restated` counter comes from the 2026-10-06 rebuild run, which read filings a few days past the 2026-09-30 census cut. It is a single count, not a read of any row past the cut.
- Every effect size and dispersion on the cards is UNVERIFIED or a register figure, never a held return. The independent review should check each.

## UNVERIFIED items
- All the literature figures in the Evidence table not marked as register claims (quoted from memory; not fetched in this pass).
- The per-event standard deviations used in each card's question 7.
- IC-B3's median announcement-to-completion time (about 80 sessions).

## Follow-up questions (not answered here)
1. Should the ingest store every statement vintage (IC-B2's build)? It already parses and counts them. That is the owner's call through the tally (D3).
2. After T164e's run and T324's seed, the counts for IC-B3 and IC-B4 can be made without a return read. If they fall short of n_eff, both cards become `deferred: power` at no further cost.
3. Whether the review merges IC-B3 with any source C card on deal-related index flows.

## Sources
- Store copy `store-20261102.duckdb` (schema 41), read-only.
- [Store census, 2026-10-28](2026-10-28-strategy-frontier-verified.md#census-re-derived); [B7 recount](2026-11-form4-insider-feasibility-recount.md); [fill-cost note](2026-10-30-paper-fill-cost-arrival.md); [earnings-event family design](2026-11-02-earnings-event-family-design.md); [claims register](claims.toml) (G2-A, G2-B, G2-2, G2-4, G2-5, G2-6, G2-7, NE-3, HO-4, QI-2, QI-19, QI-28, TP-1, TP-9).
- `src/tradepartner/ingest.py` (first-vintage rule and the `restated` counter).
