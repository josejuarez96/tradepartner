# Research Report: Cover-page facts and SIC at scale (T11c scope)

**Brief:** #174 (blocks the cover-page and header scope of plan task T11c, #163 amendment)  ·  **Date:** 2026-09-25  ·  **Status:** COMPLETE. Every candidate is answered from Tier 1 documentation, and the load-bearing FSN inference was **observed** by Probe P1 on `2026_01_notes.zip` (see [Probe P1 results](#probe-p1-results-observed-2026-09-25)).  ·  **Agent/model:** research agent, claude-opus-5-5

**Budget.** The brief gives no budget, so this report set its own: at most 25 web searches and fetches. It used 20: 6 searches, 14 fetches, and 2 PDFs read locally. It also read the repository's own recorded EDGAR fixtures, which count as primary observations.

## Answer
**Verdict:** n/a. This is a factual sourcing brief, not a return hypothesis, so SUPPORTED/MIXED grades do not apply. Each claim is tiered instead.  ·  **Confidence:** high for the FSN cover facts and SIC in the probed month (observed, Tier 1 data); medium for coverage before 2019 and for the runtime estimates.

The cheapest source of the cover facts with a point-in-time stamp is the **SEC Financial Statement and Notes (FSN) data sets**, joined by accession number to the acceptance times the adapter already caches from `submissions`:
- FSN `sub` carries `accepted` and, per filing, a `sic` "assigned by the Commission as of the filing date" (S2 §5.1).
- `num` and `txt` carry numeric and non-numeric facts keyed by accession with a dimension hash, and `dim` decodes it to `axis=member` pairs (S2 §3, §5.3 to 5.5).
- FSN's presentation table has a `CP = Cover Page` statement code (S2 §5.7).
- Scope is XBRL exhibits to all forms except 485BPOS, 497 and SDR (S2 §2), which includes the 10-K, 10-Q, 8-K, 20-F and 40-F cover pages that have been iXBRL-tagged since 2019 to 2021 (S6).
- A full backfill from 2019 is **40 files, about 14.8 GB**, instead of about 150,000 documents and hundreds of GB. It also gives **per-filing SIC with no SGML header requests** for every XBRL filing since 2009.

FSN cannot serve the nightly run: it is published monthly with an undocumented lag (S1). So new filings after the latest FSN month still need per-filing downloads, but only those, a few hundred MB per night. The companyfacts and frames APIs cannot replace FSN:
- They carry no text facts (no title, ticker or exchange) and drop dimensioned facts (S9).
- Frames also drops cover-date instants that do not align with a calendar quarter (S9).
- "Latest periodic filing per issuer-year" still needs about 37,000 or more document downloads and parses. It leaves shares up to a year stale.

## Probe P1 results (observed, 2026-09-25)
Run by team bitfly on `2026_01_notes.zip` (43,055,315 bytes; members dated 2026-02-04, so about 4 days after month end; 280 MB uncompressed: `txt` 118 MB, `num` 75 MB, `pre` 53 MB, `sub` 1.6 MB), read with DuckDB. Tier 1 (the SEC's own data, observed).

| Check | Result |
|---|---|
| (a) `txt` cover facts | **Pass.** `Security12bTitle` in 4,229 filings, `TradingSymbol` in 4,196, `SecurityExchangeName` in 4,095. About 53 % of rows carry a `ClassOfStock` dimension (`dimn > 0`); a single-class filer's facts are undimensioned. Multi-listing filers get one dimensioned triple per security, e.g. Sky Harbour: `SKYH` (Class A) and `SKYH WS` (warrants), both NYSE. |
| (b) `num` per-class shares | **Pass.** `EntityCommonStockSharesOutstanding` in 354 of 355 periodic filings (10-Q 220/220, 10-K 72/73, 20-F 33/33, 40-F 1/1, amendments 28/28). 53 filings carry more than one class, e.g. Constellation Brands 10-Q: `ClassOfStock=CommonClassA` 173,384,625 and `ClassOfStock=ConvertibleCommonStock` 26,197. |
| (c) `sub.accepted` | Eastern local time, **minute** precision (`2026-01-08 14:01:00.0`). Acceptances fall 06:00 to 22:00 with a 16:00 to 17:59 peak (2,874 of 5,428), which is EDGAR's Eastern window; UTC would run 11:00 to 03:00. Confirms: never use it for `known_at`; stamp from `submissions` by accession. |
| (d) 8-K in `sub` | **Yes.** 4,522 8-Ks (plus 101 8-K/A) of 5,428 filings; 3,849 carry `TradingSymbol`, so 8-K covers give earlier notice of ticker and exchange changes. 8-Ks carry no shares fact. |
| SIC | Present on 5,130 of 5,428 filings. Blanks are 8-Ks (170) and funds (N-CSR, 486BPOS, 424B). Every periodic filing checked has a SIC. |

What P1 adds to the design:
- **Class members are filer-custom.** Beyond `CommonClassA`, filers use extension members such as `ClassACommonStockParValue00001PerShareCustom`. The master matches classes by title and ticker (so listings are unaffected), but per-class shares need a member-to-class mapping by the listing triple of the same filing, not by member name.
- **Titles are the filer's own text**, spaces preserved; odd spellings (Constellation's `ClassA Common Stock`) come from the filing, not from FSN.
- 32 % of 10-Qs carry no `TradingSymbol`: consistent with issuers that have no 12(b) listing (not checked one by one).
- Not tested by P1: coverage before 2019 (cover tagging phase-in), and months before November 2020, which are quarterly files.

## Consumers: what each needs, and whether it is point-in-time
| Consumer | Call | Fields it reads | Point-in-time? |
|---|---|---|---|
| Classification, `store/classify.py:210` (`_evidence`) | `source.filing_headers(cik, settings.master.issuer_forms)` | `FilingHeader.sic` and `.accepted_at` only (the form comes from `filing_index`). A header with `sic=None` is skipped. Only rule 5 (`sic_6770` gives `spac`) uses SIC, and the row's `sic` column records it | **Yes.** `_classify` keeps `sics` with `known_at <= t` (classify.py:172) |
| Security master, `store/master.py:416` (`build_master`) | `source.cover_pages(cik)` | `CoverPage.accepted_at`, `.accession`, `.listings[]` of (`title`, `ticker`, `exchange`) (`adapters/filings.py:90-111`) | **Yes.** Listing `known_at = max(page.accepted_at, first.accepted_at)` (master.py:237) |
| Facts / universe (shares for cap) | `source.facts(cik, names)` with `EntityCommonStockSharesOutstanding` (`ingest.py:108`) | `FactRecord` (`as_of_date`, `class_member`, `value`, `accession`, `accepted_at`). T11c merges `CoverPageParse.facts` in because companyfacts drops the per-class values | **Yes.** `accepted_at` is the `known_at` |

`issuer_forms` = 10-K, 10-Q, 8-K, 20-F, 40-F, S-1, F-1, 10-12B, 25, 25-NSE (`config.py:166-179`). `edgar.cover_page_forms` = 10-K, 10-Q, 20-F, 40-F (`config.py:159`).

## Evidence
All web pages were seen on 2026-09-25. "Local" means the repository's recorded EDGAR fixtures (T3, owner-recorded from sec.gov), which are primary observations.

| # | Claim | Source | Tier | Key figure (quoted) | OOS / post-pub? | Net of costs? |
|---|---|---|---|---|---|---|
| F1 | FSN coverage and cadence | S1 | 1 | "updated monthly. Prior to November 2020, the data sets were updated quarterly"; coverage starts January 2009 | n/a | n/a |
| F2 | FSN scope covers all XBRL forms except a few fund and SDR forms | S2 §2 | 1 | "From Interactive Data exhibits (XBRL) to all forms other than 485BPOS, 497, SDR, SDR/A, SDR-A and SDR/W"; "Submitted from 4/15/2009"; "All data values are 'as filed.'" | n/a | n/a |
| F3 | FSN `sub.sic` is point-in-time per filing | S2 §5.1 | 1 | "Four digit code assigned by the Commission as of the filing date"; "EDGAR derived fields represent the most recent EDGAR assignment as of a given filing's submission date and do not necessarily represent the most current assignments" | n/a | n/a |
| F4 | FSN `sub.accepted` exists. **Time zone not stated** | S2 §5.1 | 1 | "DATETIME (yyyy-mm-dd hh:mm:ss)"; "Filings accepted after 5:30pm EST are considered filed on the following business day" (the only zone mentioned) | n/a | n/a |
| F5 | FSN `txt` holds every non-numeric fact, with dimensions | S2 §1, §3, §5.5 | 1 | TXT is "the plain (no HTML) text of each non-numeric XBRL fact"; key includes `dimh`; `value` "truncated to a maximum number of bytes" (max 2048) | n/a | n/a |
| F6 | FSN `num` scope wording is ambiguous about the cover page | S2 §1 vs §3 | 1 | §1: "all line item values as rendered by the Commission Viewer/Previewer"; §3: "all numeric XBRL facts presented on the primary financial statements" | n/a | n/a |
| F7 | FSN renders and labels the cover page as a statement | S2 §5.6, §5.7 | 1 | REN `menucat` "C=Cover"; PRE `stmt` "CP = Cover Page" | n/a | n/a |
| F8 | FSN dimensions decode to axis=member, with namespaces dropped | S2 §5.3 | 1 | "Each dimension is represented as the pair '{axis}={member};'"; "first characters 'Statement', last 4 characters 'Axis', and last 6 characters 'Member' … truncated"; "Namespaces and prefixes are ignored" | n/a | n/a |
| F9 | FSN co-registrant facts are flagged | S2 §5.4, §5.5 | 1 | `coreg`: "NULL indicates the consolidated entity" | n/a | n/a |
| F10 | FSN file sizes | S1 file table | 1 | e.g. "2026_08: 298.24 MB", "2026_01: 41.06 MB", "2025 Q1: 632.57 MB", "2021 Q1: 810.44 MB". Sums computed here: **2019Q1 to 2026_08 = 14.8 GB in 40 files**; 2016Q1 onward = 18.7 GB in 52 files; 2009 onward = 25.5 GB in 80 files | n/a | n/a |
| F11 | FSN files are re-issued after publication | S1 | 1 | "2010q1 through 2013q4 data sets were updated on September 26, 2024"; "2011q1 through 2013q4 … April 1, 2024"; "2019q1 data set was updated on June 15, 2021"; 2009 to 2018 "refreshed to remove character restraints and other processing fixes" | n/a | n/a |
| F12 | The smaller FS (non-notes) data sets were narrowed in Dec 2024 and **exclude the cover page** | S3; S4 §2, §5.4 | 1 | "reprocessed to 'only include the submissions and the numeric data from the primary financial statements'"; scope "(Balance Sheet, Income Statement, Cash Flows, Changes in Equity, and Comprehensive Income)"; PRE `stmt` list has no CP; no TXT table | n/a | n/a |
| F13 | Cover-page tagging: the forms and the three listing items | S6 | 1 | "tag all cover page data for Forms 10-K, 10-Q, 8-K, 20-F, and 40-F in Inline XBRL"; trading symbol, title of each class, exchange | n/a | n/a |
| F14 | Cover-page tagging phase-in | S6 | 1 | Large accelerated: fiscal periods ending on or after June 15, 2019. Accelerated: June 15, 2020. All others: June 15, 2021 | n/a | n/a |
| F15 | companyfacts `dei` holds only numeric facts: **no title, ticker or exchange** | S9 (local; the recorder keeps the `dei` namespace whole, `cli_record.py:397`) | 1 | `dei` keys in the three recordings: `EntityCommonStockSharesOutstanding` and `EntityPublicFloat` only. No `TradingSymbol`, `Security12bTitle` or `SecurityExchangeName` | n/a | n/a |
| F16 | companyfacts drops dimensioned facts, so a dual-class issuer has no shares fact | S9 (local); S11 row "Acceptance times"; S12 | 1 (local); 3 (S12) | Alphabet's `dei` has `EntityPublicFloat` only, and no `EntityCommonStockSharesOutstanding` at all. S12 (Tier 3): "the companyfacts API excludes" share-class-dimensioned facts | n/a | n/a |
| F17 | companyfacts entries carry `accn` and a `filed` date, not an acceptance time. `frame` appears only on some entries | S9 (local, Apple) | 1 | `{"accn": "0001193125-09-214859", "end": "2009-10-16", "filed": "2009-10-27", "form": "10-K" …}` has **no** `frame`, while the 10-Q with `end` 2009-06-27 has `"frame": "CY2009Q2I"` | n/a | n/a |
| F18 | Frames returns one fact per entity per calendar period, the last filed | S5 | 1 | "aggregates one fact for each reporting entity that is last filed that most closely fits the calendrical period requested" | n/a | n/a |
| F19 | API and bulk latency | S5 | 1 | submissions: "typical processing delay of less than a second"; XBRL APIs "under a minute"; bulk zips "recompiled nightly" | n/a | n/a |
| F20 | Submissions JSON has only the **current** SIC (a single scalar per CIK) | S10 (local); S5 | 1 | `submissions_plain_issuer.json`: top-level `"sic": "3571"` beside the filings arrays, with no per-filing SIC; S5: "current name, former name" | n/a | n/a |
| F21 | The SGML header has SIC and an Eastern acceptance time | S10 (local) | 1 | `<ACCEPTANCE-DATETIME>20251031060126`; `STANDARD INDUSTRIAL CLASSIFICATION: ELECTRONIC COMPUTERS [3571]`. The parser treats the time as Eastern (`adapters/edgar.py:13`) | n/a | n/a |
| F22 | Data-quality risk common to every source of `EntityCommonStockSharesOutstanding` | S7 (2022-10-06) | 1 | "scaling errors both on the cover page and balance sheet (e.g., tagging 555,555,000 shares as 555,555 shares)"; failure to tag cover shares | n/a | n/a |
| F23 | Terms | S8 (search snippet, not fetched); S1 disclaimer; S11 (10 req/s, User-Agent) | 1 | sec.gov information "is considered public information and may be copied or further distributed"; FSN "are not a substitute for such filings" | n/a | n/a |

## Candidates, field by field

### C1. FSN data sets (`{period}_notes.zip`: `sub`, `num`, `txt`, `dim`, ...)
- **Fields present:**
  - `sub`: `adsh`, `cik`, `sic`, `form`, `filed`, `accepted`, `prevrpt`, `period`, `fy`, `fp` (F3, F4).
  - `txt`: the non-numeric facts (`tag`, `version`, `ddate`, `qtrs`, `dimh`, `coreg`, `value`, `context`) (F5). This is where `Security12bTitle`, `TradingSymbol` and `SecurityExchangeName` should be, as non-numeric `dei` facts.
  - `num`: the numeric facts with `dimh`. This is where per-class `EntityCommonStockSharesOutstanding` should be.
  - **Observed by Probe P1** (2026-09-25): present, per class; see the Probe P1 results section.
- **Dimensions:** yes, via `dimh` to `dim.segments`, e.g. `ClassOfStock=CommonClassA;` (F8). Namespaces are stripped, so a member reads `CommonClassA` rather than `us-gaap:CommonClassAMember`. The store's `class_member` needs a documented normalisation. Co-registrant (`LegalEntityAxis`) facts have non-null `coreg` and are excluded, as the cover parser excludes them today (F9).
- **Acceptance time:** `sub.accepted` exists, with the zone not stated (F4). **Do not use it for `known_at`.** Stamp each `adsh` from the acceptance cache T11b already builds from `submissions` (UTC `acceptanceDateTime`). An `adsh` with no acceptance time goes to `.unstamped_*` as the existing rule says. Then the FSN time zone never matters.
- **Coverage:**
  - Filings from 2009-04-15 (F2).
  - Title, ticker and exchange exist only once each filer is phased into cover tagging: fiscal periods ending on or after 2019-06-15, 2020-06-15 or 2021-06-15 depending on filer size (F14).
  - Per-class `EntityCommonStockSharesOutstanding` predates the cover rule, because `dei` has had it since XBRL began. That this is present in FSN `num` before 2019 is **UNVERIFIED**.
- **Lag:** monthly since November 2020 (F1). Publication lag after month end is **not documented**. On 2026-09-25 the newest file is `2026_08`.
- **Size and requests:**
  - Backfill: 40 requests and 14.8 GB from 2019; 52 requests and 18.7 GB from 2016; 80 requests and 25.5 GB from 2009 (F10).
  - Nightly: 0. Monthly: 1 file of 41 to 320 MB (F10).
  - Processed one zip at a time and deleted after the `dei` rows and `sub` are extracted. Peak disk is one zip plus its extracted `sub`/`num`/`txt`/`dim` members. Uncompressed sizes are **UNVERIFIED** (estimate: low single-digit GB for the largest quarter).
- **Runtime (estimate, not measured):** download is bandwidth-bound, about 5 to 50 minutes for 14.8 GB at 5 to 50 MB/s. A columnar scan filtering `tag` to about 5 `dei` names takes about 10 to 60 s per file. Total is **roughly 0.5 to 1.5 h** for the 2019 backfill.
- **Terms:** public information, freely copyable (F23). The 10 req/s and User-Agent rules apply to the download (S11).
- **Cannot:**
  - Serve the nightly run (monthly lag).
  - Cover non-XBRL forms (S-1, F-1, 10-12B, 25) or anything before 2009.
  - Guarantee extraction correctness (SEC disclaimer, S2 §1).
  - Stay byte-stable: files are re-issued (F11), so a re-download can change extracted values for an old accession (see Caveats).

### C1'. FS data sets (the smaller, non-notes sets)
- These have the same `sub` with `sic` and `accepted`, at 57 to 122 MB per quarter (S3). Since the December 2024 reprocessing they hold **only primary-statement numbers**: no cover page and no text table (F12).
- So they are usable for **SIC only**, not for cover facts. Once FSN is downloaded for cover facts, FSN `sub` is a superset and C1' adds nothing.

### C2. Per-filing primary documents, scoped down
- **All iXBRL periodic filings (T11c as written):** about 150,000 documents at 1.5 to 2.6 MB each, about 300 GB. That is 150,000 requests (about 4.2 h of request time at 10/s) plus one `edgartools` parse each. At an assumed 1 to 2 s per parse (**UNVERIFIED**), that is 40 to 80 h. It does not fit one night.
- **Latest periodic filing per issuer per fiscal year:** about 1 in 4 of those, so about 37,000 documents, about 75 GB, and 10 to 20 h of parsing (same assumptions). Still more than one night. Shares refresh only yearly, so they go stale up to about 12 months against a universe that needs recent cap. Mid-year ticker, exchange or class changes wait for the next annual report. It is point-in-time safe but lossy.
- **Only filings whose index row shows a class change:** not possible. `form.idx` rows carry only company, form, CIK, date and file name (S11 row 8c). The index says nothing about classes. Proxies (Form 8-A12B for new 12(b) registrations, 8-K covers, 25/25-NSE) were not evaluated here (see Follow-up).
- **Fields and timestamps:** every cover fact, with class dimension, via `parse_cover_page`. `known_at` comes from the submissions acceptance time. Coverage is iXBRL from 2019 onward. Lag is none.
- **Where it fits:** only the filings **newer than the latest FSN month**. Nightly that is about 80 filings on an average business day (150,000 / 7.5 years / 250 days; estimate), about 160 MB transient and a few minutes. Peak filing days could be about 10 times that (**UNVERIFIED**).

### C3. XBRL frames (undimensioned shares) plus per-filing downloads for dual-class issuers
- **Fields:**
  - frames: `accn`, `cik`, `entityName`, `end`, `val` for one concept, one period.
  - It carries **no acceptance time**. It can be stamped via `accn` from submissions.
  - It has no text facts, so it gives no titles, tickers or exchanges (F15), and no dimensioned facts (F16).
- **Point-in-time problems:**
  - One fact per entity per calendar frame, "last filed" (F18). An original value later re-reported for the same frame is replaced.
  - Cover-date instants that do not fit a calendar quarter get no frame and are absent. Apple's 2009 10-K cover value (`end` 2009-10-16) has no `frame` (F17).
- **Verdict:** frames is a strict subset of companyfacts, which T11c already reads. It cannot supply the master's listings for any issuer, so "frames plus downloads for dual-class issuers only" still leaves every single-class issuer without title, ticker and exchange. **Not a viable source for `cover_pages`.**

### SIC without SGML headers
- **Submissions JSON / `submissions.zip`:** only the current SIC per CIK (F20). Using it for past T would leak a later SIC backward (a de-SPAC's new SIC would un-SPAC it retroactively). **Not usable** point-in-time.
- **FSN or FS `sub.sic`:** per filing, "as of the filing date" (F3), stamped from the accession's acceptance time. **Usable** for every XBRL filing since 2009, with zero header requests.
  - Its gap is the time between an issuer's registration (S-1, F-1, 10-12B) and its first XBRL filing in FSN. For a SPAC that gap runs from IPO to its first 10-Q, or to its first 8-K if 8-K covers are in FSN, which is **UNVERIFIED**.
  - During the gap, rule 5 cannot fire and the SPAC falls to `common_default`. That is a misclassification window, not look-ahead.
  - SGML headers of the registration forms close it cheaply.
- **Good enough for classification?** Yes for rule 5 at T ≥ 2016, provided registration-form headers cover the pre-periodic window and the nightly run takes ranged headers for new filings not yet in FSN. Only rule 5 reads SIC (classify.py:184).

## Disconfirmation
- **Searches run:**
  1. `"Financial Statement and Notes" data sets txt "TradingSymbol" OR "Security12bTitle" OR "EntityCommonStockSharesOutstanding" dei`
  2. `SEC financial statement data sets num.txt "EntityCommonStockSharesOutstanding" dei shares outstanding sub accepted`
  3. `SEC financial statement data sets sub.txt "accepted" field time zone Eastern …`
  4. `SEC "financial statement and notes" data set missing filings errors problems incomplete inline XBRL extraction`
  5. `SEC XBRL companyfacts frames API dimensional facts excluded …`
  6. `secfsdstools OR "financial statement and notes" "txt.txt" dei cover page TradingSymbol …`
  7. Fetches of the secfsdstools docs and README, looking for cover and `dei` handling or reprocessing notes.
  8. One attempt at a Hugging Face mirror of FSN rows (HTTP 401).
- **What was found against:**
  1. **The smaller FS data sets no longer carry the cover page** (F12). Anyone reading "FS data sets include `EntityCommonStockSharesOutstanding`" from pre-2025 material would be wrong today. This is why C1 must be FSN, not FS.
  2. **FSN's own scope wording is inconsistent** (F6). §3 says NUM is facts "presented on the primary financial statements", which would exclude the cover page. §1 and the `CP` code (F7) say it includes all rendered statements. No Tier 1 or Tier 2 source was found that shows a `dei` cover row in FSN `num` or `txt`. **Resolved by Probe P1: the cover facts are in `num` and `txt`.**
  3. **Files are restated after publication** (F11). These are processing corrections, not filer amendments, but they change the bytes of old periods.
  4. **Acceptance time zone is undocumented** (F4). No source found states it. It is irrelevant if `known_at` comes from `submissions`.
  5. **Filer tagging errors** in cover shares (F22) affect FSN, companyfacts and the document parse equally. This is not a reason to prefer one source.
  6. The only extraction-completeness statement found is the SEC disclaimer (S2 §1). No study measured FSN omissions.
  - Nothing found says FSN drops dimensioned facts: `dimh` is part of the primary key of both NUM and TXT (S2 §3).

## Caveats & gaps
- **P1 gate: passed** (one month observed, 2026-01). Had it failed, the fallback stands: if FSN `txt` lacks `Security12bTitle`, `TradingSymbol` or `SecurityExchangeName`, or `num` lacks the per-class shares, FSN still gives SIC (F3), and cover pages fall back to C2 scoped by date (see the fallback in the Recommendation).
- **FSN lag and the live run.** Facts arrive in the store up to about 1 to 2 months after acceptance unless per-filing downloads fill the gap. Stamped at acceptance, they are point-in-time correct for backtests. A live decision on day T sees them only if the gap fill ran.
- **Two sources for the same accession.** A filing parsed per-document during the lag, and later present in FSN, has two extractions. They can differ in member naming (F8) or transforms. The store needs one rule: first stored wins, or FSN replaces. Silently rewriting a stored fact would change a past `ingested_at` view.
- **Re-issued FSN files (F11).** Keying extracted rows by `(period, zip content hash, parser version)` makes a re-issue visible rather than silent.
- **Member names.** FSN strips namespaces and the `Member` suffix (F8). The master matches classes by title and ticker, not by member (master.py `_match`), so listings are unaffected. The `class_member` of shares facts needs one normalisation used by both paths.
- **8-K covers.** Cover tagging applies to 8-K (F13) and FSN's scope includes all XBRL forms (F2). Whether 8-K cover-only submissions are in FSN `sub` is **UNVERIFIED**. If they are, they give earlier notice of ticker and exchange changes and of SIC.
- All runtimes and per-day filing counts are estimates from the brief's 150,000-document figure and assumed throughput, not measurements.

## UNVERIFIED items
- ~~FSN holds the cover facts per class~~: observed by P1.
- ~~Zone of `sub.accepted`~~: Eastern, minute precision (P1).
- The publication lag of each monthly FSN file: one observation only (`2026_01`, members dated 2026-02-04); the exact data cutoff of a monthly file.
- ~~8-K in FSN `sub`~~: yes (P1).
- Per-class shares facts in FSN before 2019, and in the quarterly files before November 2020.
- Whether sec.gov honours HTTP Range on `/files/dera/...` zips (Range would allow fetching only `sub`/`num`/`txt`/`dim`).
- `edgartools` parse time per document (1 to 2 s assumed). Per-day periodic filing counts, and the peak-day multiple.
- The issuer-CIK count from T11b's owner run, which scales every per-issuer figure.
- S8's wording is from a search snippet. The page was not fetched.

## Follow-up questions (not answered here)
- Could Form 8-A12B (a new 12(b) class registration) plus 8-K cover facts replace per-filing periodic downloads as a class-change trigger?
- Is EDGAR's rendered `R1.htm` (the cover report) a small, reliable per-filing alternative to the 1.5 to 2.6 MB primary document for the lag window?
- Pre-2019 per-class shares from FSN (2009 to 2018): worth a backfill from 2016 for the dual-class market cap before cover tagging?
- The failure policy for a filing that fails every run (T11c decision 2) now also applies to FSN rows that fail parsing. Not in this brief.

## Recommendation for T11c (evidence-graded options for the owner; not a decision)

Paste-ready text (Probe P1 passed):

> **Cover pages, cover facts and SIC come from the SEC Financial Statement and Notes data sets (FSN), with per-filing fallbacks only for what FSN does not yet hold ([#174](../research/2026-09-25-cover-page-facts-at-scale.md)).**
> - **Probe P1: done 2026-09-25, passed** (results in the report's Probe P1 section). Per-class shares map to classes through the same filing's listing triples, because `ClassOfStock` members are often filer-custom.
> - **`edgar_raw.fsn_zip(period)`** streams `https://www.sec.gov/files/dera/data/financial-statement-notes-data-sets/{period}_notes.zip` into `edgar.cache_dir/fsn/` (temp file then `os.replace`, as the other bulk files). Periods: quarterly `YYYYqN` through 2025q2, monthly `YYYY_MM` from 2025_07. Enumerate them from the data set page or by probing, and treat a 404 for a month not yet published as empty.
> - **A pure parser `parse_fsn(sub, num, txt, dim)`** in `adapters/edgar.py` yields, per `adsh`: a `CoverPage` (listings grouped by `dimh`, rows with non-null `coreg` excluded); `FactRecord`s for `EntityCommonStockSharesOutstanding` (with `class_member` from `dim.segments`, normalised the same way as `parse_cover_page`); and a SIC per accession from `sub.sic`. It never reads `sub.accepted` for `known_at`: every `adsh` is stamped from the T11b acceptance cache, and unstamped ones go to `.unstamped_*`. The extracted rows are cached as JSON per period, keyed by the parser version and the zip's content hash, and the zip is deleted after extraction.
> - **`cover_pages(cik)`** returns the FSN pages for the CIK whose `sub.form` is in `edgar.cover_page_forms`, plus, for accessions in `edgar.cover_page_forms` with `isInlineXBRL = 1` accepted after the newest FSN period's cutoff and not in the parse cache, the per-filing parse as written (root `primaryDocument`, `parse_cover_page`, cache by accession, delete the document). One accession is never taken from both sources: the first stored extraction wins (owner to confirm).
> - **`facts(cik, names)`** merges FSN per-class shares in place of `CoverPageParse.facts` for FSN accessions. The collision rule and companyfacts path are unchanged.
> - **`filing_headers(cik, forms)`** returns a `FilingHeader` built from FSN `sub.sic` for every accession FSN holds (no request). It issues a ranged SGML request only for filings whose form is in `forms`, accepted on or after 1 January of `edgar.header_first_year`, and absent from FSN. Headers are cached by accession.
> - **Classification call site changes:** `store/classify.py` `_evidence` calls `source.filing_headers(cik, settings.edgar.header_forms)` instead of `settings.master.issuer_forms` (plan T9's file; tests in `tests/store/test_classify.py` updated for the new key).
> - **New config keys (with default tests):** `edgar.fsn_first_year` (default **2015**, one year before the 2016 price start, so the latest SIC before any T ≥ 2016 is present; 2019 if SIC before 2016 is not needed; 2009 for full history), `edgar.header_forms` (default `[S-1, F-1, 10-12B, 10-K, 10-Q, 20-F, 40-F]`; periodic forms cost a request only in the FSN lag window), `edgar.header_first_year` (default = `edgar.fsn_first_year`). `edgar.cover_page_forms` is unchanged (add `8-K` if the owner wants earlier listing changes: P1(d) found 8-K covers in FSN, 3,849 with a ticker in one month).
> - **Expected cost (estimates; the PR records measured values):**
>   - **Backfill:**
>     - FSN from 2015: 56 files, about 19.9 GB transient download, about 0.5 to 2 h. From 2019: 40 files, 14.8 GB.
>     - Plus per-filing cover documents and ranged headers for the lag window: about 1 to 2 months of periodic filings, about 2,000 to 8,000 documents and 4 to 16 GB transient, up to a few hours of parsing.
>     - Plus registration-form headers since `header_first_year`: about 10,000 to 25,000 ranged 4 KB requests, 17 to 45 min at 10/s.
>     - Peak disk: one FSN zip plus its members (a few GB). Retained: the parse caches (MBs to low GB).
>   - **Nightly:**
>     - 0 FSN requests, except one monthly file of 41 to 320 MB when it appears.
>     - About 80 cover documents on an average day (about 160 MB transient, minutes).
>     - About 80 plus new-registration ranged headers (seconds).
>     - Peak filing days are about 10 times that.
> - **Fallback if P1 fails for cover facts:** keep FSN for SIC (P1 does not affect F3), and limit per-filing cover downloads to `edgar.cover_page_first_date` onward. For history, take one periodic filing per issuer per fiscal quarter-end month. This is still about 150,000 documents since 2019, so the owner must choose between a multi-night resumable backfill (a per-run document cap, `edgar.max_cover_downloads_per_run`) and accepting the `snapshot_static` listings until it completes.
>
> **Open questions for the owner:** (1) the P1 result; (2) which source wins when an accession has both an FSN and a per-document extraction; (3) `edgar.fsn_first_year`: 2015, 2016 or 2019; (4) whether the lag-window gap fill runs nightly (live runs see new shares within a day) or is left to the next FSN month (backtests are unaffected, live runs see facts up to about 2 months late); (5) whether FSN re-issues (F11) re-extract old periods or are ignored once a period is cached.

## Sources
All were seen on 2026-09-25.
- **S1** SEC, Financial Statement and Notes Data Sets (file table, update notes, "Last updated August 31, 2026"): https://www.sec.gov/data-research/sec-markets-data/financial-statement-notes-data-sets (Tier 1)
- **S2** SEC, Financial Statement and Notes Data Sets documentation (PDF): https://www.sec.gov/files/aqfsn_1.pdf (Tier 1). Sections §1, §2, §3, §5.1 to 5.7 read in full.
- **S3** SEC, Financial Statement Data Sets page (December 2024 reprocessing note, sizes): https://www.sec.gov/data-research/sec-markets-data/financial-statement-data-sets (Tier 1)
- **S4** SEC, Financial Statement Data Sets documentation (PDF): https://www.sec.gov/files/financial-statement-data-sets.pdf (Tier 1)
- **S5** SEC, EDGAR Application Programming Interfaces: https://www.sec.gov/search-filings/edgar-application-programming-interfaces (Tier 1)
- **S6** SEC, FAST Act Modernization and Simplification of Regulation S-K, small business compliance guide (cover-page tagging): https://www.sec.gov/resources-small-businesses/small-business-compliance-guides/fast-act-modernization-simplification-regulation-s-k (Tier 1)
- **S7** SEC, Scaling and Tagging Errors for Entity Common Stock Shares Outstanding (2022-10-06): https://www.sec.gov/newsroom/whats-new/osd-announcement-2210-dqreminder-entitycommonstocksharesoutstanding (Tier 1)
- **S8** SEC, Privacy Information / website policies (search snippet only): https://www.sec.gov/about/privacy-information (Tier 1, not fetched)
- **S9** Local recordings: `tests/fixtures/edgar/company_facts_{plain_issuer,dual_class,delisted_25nse}.json` (T3, recorded from sec.gov, `dei` kept whole per `cli_record.py`) (Tier 1, primary observation)
- **S10** Local recordings: `tests/fixtures/edgar/submissions_*.json`, `sgml_header_*.txt` (Tier 1, primary observation)
- **S11** [2026-09-25-free-data-terms.md](2026-09-25-free-data-terms.md): EDGAR rate limit, User-Agent, index fields, bulk zips, owner recording notes (Tier 1 via its sources)
- **S12** OpenBB PR #7660, "Resolve per-share facts tagged only with share-class dimensions": https://github.com/OpenBB-finance/OpenBB/pull/7660 (Tier 3; tooling corroboration only, via search summary)
- Also consulted, nothing relevant found: secfsdstools docs https://hansjoergw.github.io/sec-fincancial-statement-data-set/ and README https://github.com/HansjoergW/sec-fincancial-statement-data-set (Tier 3); Hugging Face FSN mirror (HTTP 401).
