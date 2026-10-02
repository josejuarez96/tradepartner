# Research Report: EDGAR data-quality pitfalls at scale, from open-source EDGAR libraries

**Brief:** #572  ·  **Date:** 2026-10-02  ·  **Status:** COMPLETE. Every source the brief names is covered, and the gaps are listed under Caveats.  ·  **Agent/model:** research agent, claude-opus-5-5

**Budget.** The brief sets no number, so this report set its own cap of 50 web searches and fetches. It used 41: 7 searches and 34 fetches. It also read the repository's code and recorded EDGAR fixtures, which count as primary observations.

**Grading.** The verdict scheme in handoff §6.1 is built for return hypotheses: its out-of-sample and costs tests do not apply to data-quality facts. So each pitfall's grade here keeps §6.1's source-count rule and drops those two tests:
- **SUPPORTED:** 3 or more independent sources, at least 2 of them Tier 1.
- **MIXED:** the sources conflict.
- **INSUFFICIENT:** fewer than 2 qualifying sources.

Tiers used here:
- **Tier 1:** SEC pages, our own observed failures and fixtures, and a library's own source code (evidence of that library's behaviour only).
- **Tier 3:** library issue trackers and documentation. They are user reports, allowed here because this is a tooling-status question.

## Summary table (pitfall first; detail and file:line in the final mapping table)

| # | Pitfall | Source | Frequency (observed) | How libraries handle it | Our status | Recommended policy |
|---|---|---|---|---|---|---|
| P1 | Transient 503, 429, connection drop or timeout | all HTTP | 1 × 503 and 1 network drop in one backfill (#554) | edgartools: exponential retry, 5 to 8 attempts (429 excluded); secedgar: urllib3 `Retry`; sec-edgar-downloader and python-edgar: none | **Partly**: one retry, statuses only, no retry on transport errors | **Retry** (#554) |
| P2 | Rate-limit block (403 or 429) that lasts until the rate stays under the threshold for 10 minutes | all HTTP | 429 reported at 9.7 to 9.9 req/s (secedgar #280) | edgartools raises `TooManyRequestsError` at once; secedgar reports "banned … 10 minutes" | **Partly**: 403 is retried after 1 s, which is too short | **Retry once after a long wait, then fail** |
| P3 | Degraded result cached after an HTTP error | SGML/.txt | edgartools #1349 and #1351 (fixed) | edgartools moved from fallback to **raise on any HTTP status** | **Handled**: no fallback, and only 404/410 are recorded as failures | **Fail** (keep) |
| P4 | Bulk zip member that is `{}` or has no `cik` | companyfacts.zip, submissions.zip | 70 of 20,425 members; 5,387 keyless pages by design (#566, #567) | No library evidence found | **Handled** (#567) | **Repair from the API** (keep) |
| P5 | Per-CIK payload malformed or non-JSON (HTML or empty 200) | submissions API, companyfacts API | Not observed by us; secedgar #310 and edgartools #443 | secedgar: fixed (#315); edgartools: crash from a corrupt local cache | **Not handled**: fails the whole source | **Quarantine with allowance, per CIK** |
| P6 | form.idx data row that does not parse | full-index | 1 row in the 2016+ backfill (#358) | python-edgar: no validation (reads `master.idx`, decodes latin-1); edgarjure: switched to `master.idx` | **Partly**: blank name fixed; any other bad row fails the source | **Quarantine with allowance, plus repair from submissions** |
| P7 | Non-UTF-8 byte in a "UTF-8" data set | FSN `txt.tsv` | 1 byte (0x92) found (#455) | python-edgar: latin-1 decode (never fails, silently garbles text); secfsdstools: not found | **Handled**: U+FFFD, and an accession whose kept value has one fails | **Repair only in free text; fail the accession if a kept field has it** (keep) |
| P8 | Unquoted tabs inside a value | FSN `txt.tsv`, `dim.tsv` | 1 line, 2015q1 (#498) | Not found | **Handled**: folded into the free-text column only | **Repair in free text only** (keep) |
| P9 | Data-set schema or page-layout drift | FSN/FS data sets, FSN index page | SEC added `segments` and narrowed FS in 2024 (secfsdstools #16); page regex broke (secfsdstools #15) | secfsdstools: detects it and tells the user to reset; #15 crashed | **Handled**: a missing column or no periods raises | **Fail** (keep), caught by the pre-flight |
| P10 | Truncated or corrupt bulk download | all zips, gz | Not observed by us | edgartools: retries `BadGzipFile`/`EOFError`, 8 bulk attempts, warns on a Content-Length mismatch | **Not handled**: a `BadZipFile` fails the run, with no re-download | **Retry** (re-download once) |
| P11 | Filing `.txt` empty or truncated (0 bytes) | SGML header | 1 filing, repeatable (edgartools #672) | edgartools: falls back to the filing index page (#674) | **Handled**: `ValueError`, then the `_guarded` quarantine | **Quarantine** (keep) |
| P12 | Acceptance-time zone confusion | SGML (Eastern, no zone), FSN `accepted` (Eastern), submissions (UTC `Z`) | edgartools #557: a 5-hour error, open | edgartools: bug open | **Handled**: submissions UTC only; SGML Eastern with fold=1 | **Fail/unstamped; never repair** |
| P13 | Duplicate or conflicting fact values; amendments | companyfacts, FSN | edgartools #701, #769, #1295, #1196 | edgartools: various dedupe fixes, some silent drops | **Handled**: the collision key is withheld and recorded | **Quarantine the key** (keep) |
| P14 | Ticker map is current-only and "not guaranteed" | company_tickers*.json | By design (SEC FAQ) | edgartools: declined a local cache (#713) | **Handled**: `snapshot_static`; a row without ticker or exchange is skipped | **Skip and count; a malformed row is quarantined** |
| P15 | Form 25 / 25-NSE quirks (filed by the exchange, no effective date, amendments) | delistings | By design | No library evidence found | **Handled** | Keep |
| P16 | FSN files re-issued after publication; bulk zips trail the day | FSN, bulk zips | SEC notes re-issues (prior report F11) | secfsdstools: reset on change | **Handled**: counted, top-up per CIK | Keep |

## Top 5 recommendations (paste-ready for the PR body)
1. **Transient HTTP failures (P1, P2, P10): retry.** Use exponential backoff with a cap and honour `Retry-After`. Retry 503, 429, connection resets, timeouts and mid-stream disconnects. For a bulk zip, retry `BadZipFile` or a CRC error by re-downloading once. Treat 403 as SEC's block, which lasts until the rate has stayed under the threshold for 10 minutes: one wait of at least 10 minutes, then fail. Never cache a degraded result. *Owner decision: no* (#554 already asks for this; only the cap values are config).
2. **Per-CIK payload failures (P5): quarantine with an allowance.** A submissions or companyfacts payload that does not parse, or is non-JSON, empty or keyless from the API, currently fails the whole source. Extend T11h's `failed_filings.json` to CIK keys under the same counted-day rule and share threshold. A quarantined CIK is reported and never read as "no facts". *Owner decision: yes* (the allowance, and whether a quarantined CIK may enter the universe).
3. **form.idx rows that do not parse (P6): quarantine the row with an allowance, then repair from submissions.** Pull the accession and CIK out with the regex `edgar/data/(\d+)/(\d{10}-\d{2}-\d{6})\.txt`, and take form and acceptance from that CIK's submissions, the same source `known_at` already comes from. A row with no recoverable accession fails. *Owner decision: yes* (the allowance; whether a dropped 25 or 25-NSE row may ever count against it).
4. **A pre-flight `validate-inputs` sweep (P4 to P9): fail before ingest, listing every bad record in one pass.** Run every bulk and index parser in count-only mode (form.idx quarters, both bulk zips, FSN periods) and write one report of every bad record. Each past failure cost a full rerun of about 24 minutes or more (#566). *Owner decision: yes* (scope: whether it is a separate command or the first phase of `backfill`).
5. **Never repair time or value fields (P12, P13).** Acceptance times come only from submissions (UTC). FSN `accepted`, companyfacts `filed` and an SGML time are never substituted. Encoding repair is allowed only in free-text columns, with U+FFFD failing any kept value. Conflicting values are withheld, never picked. *Owner decision: no* (this is the existing rule; record it as policy).

## Answer
**Verdict:** n/a as a single grade. This is a factual sourcing brief. Each pitfall is graded in the Evidence table: 8 SUPPORTED, 2 MIXED, 6 INSUFFICIENT for library handling.  ·  **Confidence:** high for our own failures and for SEC's documented behaviour; medium for library behaviour (code read at one point in time on `main`, line numbers approximate); low for how often problems occur outside our own runs.

The mature libraries handle **transport** failures far better than **record** failures:
- **Retries.** edgartools retries network and gzip errors with exponential backoff (5 attempts, 8 for bulk downloads).
- **Fail closed on HTTP errors.** edgartools recently made every HTTP error status *raise* rather than fall back. A silent fallback had cached all-`None` headers on a 5xx, 429 or 403 (#1349, #1351).
- **Record-level problems.** No library found offers a record-level quarantine, an allowance, or a pre-flight sweep. They crash (secedgar #310, edgartools #443), silently garble the record (python-edgar's latin-1 decode), or silently drop rows (edgartools #1072, #1196).

Our adapter already beats them on record failures that happen inside a single filing:
- per-accession FSN failures;
- the T11h counted-day quarantine;
- collision withholding;
- U+FFFD fail-on-kept.

The two real gaps:
1. **Per-CIK and per-row failures** (submissions and companyfacts payloads, form.idx rows, ticker rows) still fail the whole source.
2. **Transport retries** are a single short retry.

## Our failures so far (primary observations, Tier 1)
- **#358** (closed): the form.idx row `SC 13D … 1036125 1997-03-24 edgar/data/1036125/0000950134-97-002093.txt` has a blank company name. It raised `ValueError: form.idx row does not parse`.
- **#455** (closed by #461): FSN `txt.tsv`, SandRidge 10-K/A, line 6850. A Windows-1252 byte 0x92 made DuckDB fail with "Invalid unicode (byte sequence mismatch) … This file is not utf-8 encoded." Zero rows were written.
- **#498** (closed): FSN 2015q1 `txt.tsv` line 6850 (accession `0001349436-15-000028`) has "29 tabs instead of expected 19", from unquoted tabs in the `AmendmentDescription` value.
- **#566** (closed by PR #567, merged as `10905e6`):
  - "approximately 70 empty JSON objects `{}` among 20,425 total members" of `companyfacts.zip`.
  - The run ended with `"edgar: failed, 0 rows, cursor since=2016-01-01: KeyError: 'cik'"` after about 24 minutes.
  - PR #567's sweep found the same shape in `submissions.zip` CIK members, and found that "5,387 pages have no `cik` by design".
  - This is the `KeyError: 'cik'` of 2026-10-02 that the brief lists as open. The issue search shows #566 closed, and no other `KeyError` issue exists apart from #573 (traceback capture) and this brief.
- **#554** (open, no PR): "a network drop while the Mac slept" and "SEC `503 Service Unavailable` for one filing header". The client "retries a 503 once after 1 s, then fails the whole chunk, and the run with it".

## Evidence
All pages were seen on 2026-10-02. "Local" means this repository's code or recorded fixtures.

| # | Claim | Source | Tier | Key figure (quoted) | Grade |
|---|---|---|---|---|---|
| E1 | SEC's rate ceiling | S1 Developer FAQ; S2 Accessing EDGAR Data; S3 2021 announcement | 1 | "Our current maximum access rate is 10 requests per second"; S3: SEC "may limit further requests from the relevant IP address(es) for a brief period" | SUPPORTED |
| E2 | The block lasts until the rate has been below the threshold for 10 minutes | S4 (SEC block-page text, seen via a mirror and a search snippet); S5 secedgar `client.py`; S6 edgartools docs | 1 (S4, text unverified on sec.gov) / 1 (S5 code) / 3 (S6) | S4: "Once the rate of requests has dropped below the threshold for 10 minutes, the user may resume"; S5: "SEC has banned your IP for 10 minutes"; S6: "typically 10 minutes to 24 hours" (no source cited) | MIXED (duration agreed; S6's 24 h is unsourced) |
| E3 | A 429 can occur below 10 req/s | S7 secedgar #280 | 3 | the user reported "9.7-9.9 requests per second" and got 429 | INSUFFICIENT (one report) |
| E4 | edgartools retries transport errors, not 429 | S8 `edgar/httprequests.py` (about L62-74, L96-109, L409-410) | 1 (code) | `RETRYABLE_EXCEPTIONS = (RequestError, HTTPError, TimeoutException, ConnectError, ReadTimeout, … RemoteProtocolError, EOFError, gzip.BadGzipFile)`; 5 attempts for quick requests, 8 for bulk; backoff `wait_exp_base=2`, maximum 16 s and 120 s; on 429 it raises `TooManyRequestsError`, which is not in the tuple | SUPPORTED as code; **contradicts S6**, which says 429 is retried |
| E5 | sec-edgar-downloader has a rate limiter and no retry | S9 `_sec_gateway.py` (about L12-26) | 1 (code) | `Rate(SEC_REQUESTS_PER_SEC_MAX, Duration.SECOND)`; a bare `requests.get` then `resp.raise_for_status()` | INSUFFICIENT (one library) |
| E6 | secedgar retries via urllib3 and caps the rate at 10 | S5 `client.py` (about L79-103, L141) | 1 (code) | `Retry` with `retry_count`, `backoff_factor`, `raise_on_status=True`; rate "0 < value <= 10" | INSUFFICIENT (one library) |
| E7 | python-edgar reads `master.idx` split on `\|`, decoded latin-1, with no retry and no row validation | S10 `edgar/main.py` (about L4, L66-71) | 1 (code) | `SEP = "\|"`; `lines = lines.decode("latin-1")`; no retry | INSUFFICIENT (one library) |
| E8 | A silent fallback on an HTTP error cached degraded headers, and was replaced by raising | S11 edgartools #1349; S12 #1351 (PR #1358) | 3 | #1349: "Nothing raises. Callers cannot distinguish 'SEC was briefly unavailable' from 'this filing has no acceptance time'"; fix `if is_unreachable(e) or http_status(e) is not None: raise` | SUPPORTED as a library decision (2 issues and a PR in one library); a single library |
| E9 | A filing's `.txt` can be empty (0 bytes), repeatably | S13 edgartools #672; S14 #674 | 3 | "SEC returned an empty or truncated response (0 bytes)" for `0001045810-21-000064`; #674 falls back to the filing index page and loses "`FilingHeader` metadata from SGML header" | INSUFFICIENT (one filing, one library) |
| E10 | Non-JSON or empty 200 bodies reach JSON parsers | S15 secedgar #310 (fixed by PR #315); S16 edgartools #443 | 3 | "JSONDecodeError: Expecting value: line 1 column 1 (char 0)" in both | SUPPORTED that it occurs (2 libraries, Tier 3), but the cause is unclear (S16 is a corrupt local cache) → INSUFFICIENT as a property of SEC's API |
| E11 | Empty `{}` members in companyfacts.zip, and keyless pages in submissions.zip | Local: #566, PR #567 | 1 | "approximately 70 empty JSON objects `{}` among 20,425"; "5,387 pages have no `cik` by design" | SUPPORTED as an observed fact (one dated zip); how many appear in other nights' zips is **UNVERIFIED** |
| E12 | FSN members are not always UTF-8 and can hold unquoted tabs | Local #455, #498; S17 SEC FS/FSN docs say UTF-8 and tab-delimited (search snippet) | 1 | 0x92 at line 6850; "29 tabs instead of expected 19". S17 snippet: "UTF-8 encoding, tab-delimited format, with lines terminated by \n" | **MIXED**: SEC documents UTF-8, and the data violated it once each |
| E13 | SEC data-set schemas change | S18 secfsdstools #16; prior report F12 (FS narrowed, Dec 2024) | 3 / 1 | #16: SEC introduced "a new 'segments' column"; the library "detects problematic data and instructs users to clear and reload" | SUPPORTED (Tier 1 via the prior report, plus Tier 3) |
| E14 | Scraping the data-set listing page breaks | S19 secfsdstools #15 | 3 | `self.table_re.findall(content.text)[0]` → "IndexError: list index out of range" | INSUFFICIENT |
| E15 | Submissions `acceptanceDateTime` is true UTC | Local: SGML fixtures against submissions fixtures | 1 | SGML `20251031060126` (EDT) against `2025-10-31T10:01:26.000Z`; `20260204215603` (EST) against `2026-02-05T02:56:03.000Z`; `20260924100840` (EDT) against `2026-09-24T14:08:40.000Z` | SUPPORTED (3 of 3 exact matches; matched by timestamp, since the submissions arrays are columnar) |
| E16 | Libraries get acceptance-time zones wrong | S20 edgartools #557 (open) | 3 | VMAR: SEC "2025-12-19 17:14:45", edgartools "2025-12-19 22:14:45" | INSUFFICIENT (one report) but directly relevant to `known_at` |
| E17 | Amendments and duplicates in companyfacts mislead naive dedupe | S21 edgartools #701; #769; #1295; #1196 | 3 | #701: amended filings "often lack complete XBRL data"; #1196 "silently drops facts when units or concept labels collide" | SUPPORTED as a library-bug class (4 issues) |
| E18 | Fixed-width text parsers silently drop rows | S22 edgartools #1072 | 3 | "only 15 of 192 stated positions parsed successfully (8% recovery)" (13F text, not form.idx) | INSUFFICIENT for form.idx; an analogue only |
| E19 | The ticker map is not guaranteed | S1 Developer FAQ | 1 | ticker files are "updated periodically but do not guarantee accuracy or scope" | SUPPORTED with the prior reports (current-only, `snapshot_static`) |
| E20 | Filings appear 1 to 3 minutes after acceptance, longer under load | S1 | 1 | "often available on sec.gov within 1-3 minutes of the EDGAR system timestamp. The lag time can increase significantly with high server load" | INSUFFICIENT alone; consistent with prior report F19 |
| E21 | The ticker-map outage case is not handled by edgartools | S23 edgartools #713 | 3 | "the mapping of companies to CIK numbers is not possible, due to the tickers.txt file not being available"; closed "not planned" | INSUFFICIENT |
| E22 | Index update timing | S2 | 1 | "updated nightly starting about 10:00 p.m., ET; the process is usually completed within a few hours" | SUPPORTED with prior report 8c |

## Pitfalls in detail

**P1/P2/P10. Transport.**
- **What goes wrong:** SEC sheds load with 503s and rate-limits with 403 or 429. A laptop sleep drops connections, and a large zip can end mid-stream.
- **Libraries:**
  - edgartools retries every httpx transport error, gzip `EOFError` and `BadGzipFile`, with exponential backoff, and does not retry 429 (E4).
  - secedgar retries through urllib3 (E6).
  - sec-edgar-downloader and python-edgar do not retry at all (E5, E7).
- **SEC's rule:** a block lifts only after the rate has been under the threshold for 10 minutes (E2). A 1-second retry of a 403 therefore cannot succeed, and its request extends the block.
- **Ours:**
  - `_get` retries once on 403/429/503 after `Retry-After` or `edgar.retry_backoff_seconds` (`edgar_raw.py:176-199`).
  - Streams retry once on status only (`edgar_raw.py:438-457`).
  - `httpx.TransportError` is never retried.
  - A truncated zip raises `BadZipFile` when it is opened (`edgar_source.py:461`, `:1623`, `:1817`). The download records no `Content-Length` check.

**P3. Degraded fallbacks.** edgartools' fallback to the filing index page is a cautionary case (E8). It turned "SEC unavailable" into "this filing has no acceptance time", and the result was memoised. The fix was to raise on any HTTP status. Our `_guarded` already skips only 404/410 and parser `ValueError`s, and lets every other status propagate (`edgar_source.py:1207-1229`). Keep that.

**P4/P5. Bulk and per-CIK JSON.**
- **Already fixed:** #567 handles `{}` and keyless members in both zips by asking the per-CIK API (`edgar_source.py:467-479`, `:1631-1635`, `:1652-1656`).
- **Still open:** the per-CIK paths.
  - `_fetch_submissions` → `reduce_submissions` raises `ValueError` on a malformed payload (`:220-241`, `:440-453`).
  - `_company_facts_payload` → `_holds_accession` would raise `KeyError` on an API 200 `{}` (`:1649`, `:1662`), and `parse_company_facts` raises `ValueError` on one bad entry (`edgar.py:344-378`).
  - None of these go through `_guarded`, so one CIK fails the source (`ingest.py:284-287`).
- **Libraries:** none handles this better. They crash (E10).

**P6. form.idx.**
- **Our parser:**
  - It raises on any data row that fails both regexes (`edgar.py:278-283`).
  - The #358 fix added a blank-name pattern (`edgar.py:263-266`).
- **python-edgar** avoids fixed-width parsing by reading the pipe-delimited `master.idx`, with no validation and a latin-1 decode that never fails (E7). edgarjure moved to `master.idx` for the same reason (search result, Tier 3).
- **Unknown:** how many other malformed shapes exist in 1993 to 2026 (**UNVERIFIED**). The pre-flight sweep would measure it.
- **Repair route:** the accession and CIK sit in a fixed tail pattern, so they can almost always be recovered even when the name or form columns are garbled. Form and acceptance can then come from that CIK's submissions record, which is already the `known_at` source (`edgar_source.py:417-438`).

**P7/P8. FSN bytes and tabs.**
- **Our handling:**
  - Invalid bytes become U+FFFD and surplus tabs fold into the named free-text column only (`edgar_source.py:1869-1906`).
  - The parser fails an accession whose kept listing value or class segment carries U+FFFD (`edgar.py:715-722`, `:754-757`).
  - One accession's failure never fails the period (`edgar.py:811-830`).
  - A line with too few fields, or surplus tabs in a non-free-text member, still fails the whole read on purpose (`edgar_source.py:1881-1883`).
- **Comparison:** this is stricter and more transparent than any library behaviour found.
- **Remaining risk:** a fewer-fields line or a surplus tab in `sub.tsv` or `num.tsv` fails the whole period. That is correct because shifting columns silently is worse, but the pre-flight should list such lines.

**P9. Schema and page drift.** SEC changed the FSN/FS layouts in 2024 (E13). `_fsn_rows` selects named columns, so a renamed column fails loudly (`edgar_source.py:1853-1862`). `fsn_periods` raises when the page lists none (`edgar_raw.py:521-523`), and `_fsn_extract` raises on a missing member (`edgar_source.py:1820-1823`). Fail is the right policy: a silent schema change would corrupt every row.

**P11. Empty SGML `.txt`.** `parse_sgml_header` raises on a range with no `</SEC-HEADER>` (`edgar.py:399-401`). Under `_guarded` this is a counted per-document failure, quarantined after `edgar.max_filing_failures` identical days (`edgar_source.py:1195-1205`). That matches the evidence of a repeatable, filing-specific defect (E9), and is better than edgartools' fallback, which drops the header.

**P12. Time zones.**
- Three EDGAR clocks exist:
  - SGML: Eastern, no zone.
  - FSN `accepted`: Eastern, minute precision (prior report P1(c)).
  - Submissions: true UTC (E15).
- edgartools still has an open 5-hour error (E16).
- **Ours:**
  - Stamps only from submissions (`edgar.py:120-123`, `:205-216`).
  - Parses SGML as Eastern with `fold=1` (`edgar.py:406-409`).
  - Never reads FSN `accepted` (`edgar.py:603-609`).
- **Repairing a time is the one repair that directly risks `known_at`.** A missing stamp must stay unstamped (`edgar_source.py:431-434`).

**P13. Duplicates and conflicts.**
- **Ours:**
  - Fails an FSN accession with two values for one tag and class (`edgar.py:758-759`, `:784-785`).
  - Withholds a cross-source collision key (`edgar_source.py:1500-1529`).
  - Caps a company-facts `end` at the acceptance date, so an XBRL date typo never dates a fact after it was known (`edgar_source.py:1595-1596`). That repair is `known_at`-safe because it moves only `as_of_date`, and only earlier.
- **Libraries** have repeatedly shipped silent-drop or wrong-pick bugs in this area (E17).

**P14. Ticker map.** `parse_company_tickers` skips rows with no ticker or exchange (`edgar.py:310-312`), but raises on any other malformed row, such as a non-numeric CIK (`edgar.py:299-322`, `_cik` at `:107-108`). The snapshot is current-only and stamped at fetch time, so skipping one malformed row with a count loses nothing point-in-time.

**P15. Form 25.**
- **Ours:**
  - The exchange's own copy of a 25-NSE is dropped and the subject company kept (`edgar_source.py:185-187`; module docstring `:14-16`).
  - Amendments are included (`store/delistings.py:74-77`).
  - The effective date defaults to filing plus 10 days under Rule 12d2-2 (`store/delistings.py:79-80`, `:175-176`).
  - An unmatched class is returned, never guessed (`store/delistings.py:20-23`).
  - A notice missing a required field raises inside `_guarded` (`edgar.py:585-589`).
- **Libraries:** no library evidence was found on Form 25 handling (INSUFFICIENT).

**P16. Re-issues and trailing bulk files.** FSN re-issues are detected and counted but never re-extracted (`edgar_source.py:598-622`). Bulk zips that trail `latest` fall back to the API and are marked incomplete (`edgar_source.py:1636-1649`).

## Disconfirmation
- **Searches run:**
  1. "edgartools github issue companyfacts KeyError";
  2. SEC "Request Rate Threshold Exceeded" 10 minutes 403;
  3. sec-edgar-downloader retry 429 backoff;
  4. "sec.gov" "limited for 10 minutes" undeclared automated tool;
  5. FSN "UTF-8" tab-delimited documentation;
  6. form.idx fixed-width parsing problems, `master.idx` pipe;
  7. companyfacts duplicate facts and wrong fy/fp;
  8. edgartools issue searches: `sgml` (closed), `facts error`, `ticker cik`, `companyfacts`, `submissions json`;
  9. secedgar closed issues; secfsdstools all issues;
  10. code reads of edgartools `httprequests.py`, sec-edgar-downloader `_sec_gateway.py`, secedgar `client.py` and python-edgar `main.py`.
- **"Do these libraries actually fail closed anywhere, and why?"** Yes, and that cuts against blanket tolerance.
  1. edgartools **removed** a fallback and now raises on any HTTP status (E8): degrading silently made an outage look like missing data, and memoisation made it stick.
  2. secfsdstools fails and asks the user to reset when the schema changes (E13).
  3. edgartools raises at once on 429 rather than retrying (E4).

  The pattern is to fail closed on **transport and schema** problems and on anything whose degraded output looks like a valid answer. Libraries do not fail closed on record problems; they crash or drop.
- **What was found against a quarantine-with-allowance policy:** no library implements one, so there is no external evidence that it is standard practice (INSUFFICIENT). The arguments for it here come from our own failure history, not from the libraries. The case against it is E8's lesson: a skipped record must never look like "no data". T11h already enforces that with reported counts, and recommendation 2 keeps that rule.
- **Found against "libraries handle this":**
  - edgartools' documentation says 429 is retried, but its code raises (E4 against S6). Library documentation is therefore not evidence.
  - python-edgar's latin-1 decode never fails, but on a non-latin-1 byte it garbles the text silently (E7). "Never crashes" is not the same as "correct".
  - Silent drops are a recurring bug class in edgartools (E17, E18).
- **Found against "10 req/s is safe":** a 429 at 9.7 to 9.9 req/s (E3). The default `edgar.requests_per_second = 10` is at the ceiling. edgartools defaults to 9 (S6, Tier 3).
- **Nothing found** on `{}` members in companyfacts.zip in any library tracker. Our observation (E11) is the only evidence.

## Caveats & gaps
- Library line numbers come from fetching `main` on 2026-10-02 and are approximate. The links point at files, not pinned commits.
- No issue tracker of sec-edgar-downloader or python-edgar was mined, only their code.
- No dedicated companyfacts parser other than edgartools was examined.
- Frequencies outside our own runs are anecdotal: one issue each.
- The 10-minute block text (E2, S4) was read on a mirror and in a search snippet of SEC's block page, not fetched from sec.gov.
- Whether a 403 from SEC is a block or a missing User-Agent cannot be told apart by status alone. Our client raises before sending if the User-Agent is unset (`edgar_raw.py:132-139`), so a 403 at run time is a block.

## UNVERIFIED items
- The SEC block-page wording on sec.gov itself (S4).
- How many `{}` members appear in other nights' `companyfacts.zip` (E11 covers one zip).
- The number of malformed form.idx rows over 1993 to 2026, beyond #358.
- Whether SEC adds rows to a full-index quarter after `edgar.index_settle_days`. If it does, a cached settled quarter would miss them. No source found either way.
- Whether a 200 response with an `{}` or HTML body ever comes from `data.sec.gov` (E10's causes are unclear).
- Whether `httpx` raises on a stream cut short of its `Content-Length` in every case (assumed: `RemoteProtocolError`).
- edgartools #672: the issue page read as open, although it was listed in a closed search.

## Follow-up questions (not answered here)
- Should `edgar.requests_per_second` default below 10, given E3 and edgartools' 9?
- Should FSN periods whose `sub.tsv` or `num.tsv` have structural line errors become a per-line quarantine, rather than failing the whole period?
- Should #573 (a traceback in failed run rows) include the offending record's key (accession, CIK, zip member, line number), so each failure names its record?

## Mapping table: each pitfall against our code

Policy key: **F** = fail, **Q** = quarantine with allowance, **R** = repair, **T** = retry. "PIT risk" flags where a repair could change `known_at`.

| # | Pitfall | Status | Where (file:line) | Recommended policy | PIT risk of a repair | Owner decision? |
|---|---|---|---|---|---|---|
| P1 | 503, 429, connection, timeout | **Partly** | `adapters/edgar_raw.py:44`, `:176-199` (one retry), `:438-457` (stream, status only); no `TransportError` retry anywhere | **T**: exponential backoff, capped, honouring `Retry-After` (#554) | None | No (only the cap values) |
| P2 | 403 block (10 min) | **Partly** | `edgar_raw.py:44` treats 403 like 503; `:142-173` | **T** once after ≥ 10 min, then **F** | None | No |
| P3 | Degraded result after an HTTP error | **Handled** | `adapters/edgar_source.py:1207-1229` (`_guarded`: only 404/410 and `ValueError` skipped) | **F** (keep) | n/a | No |
| P4 | `{}` or keyless bulk member | **Handled** | `edgar_source.py:467-479`, `:1631-1635`, `:1652-1656` | **R** from the per-CIK API (keep) | None: the stamp still comes from submissions | No |
| P5 | Malformed or non-JSON per-CIK payload (submissions or companyfacts API, a bad facts entry) | **Not handled** | `edgar_source.py:220-241`, `:440-453`, `:1638-1649`, `:1659-1670`; `adapters/edgar.py:344-378`; failure propagates via `ingest.py:284-287` | **T** once, then **Q** per CIK, counted, threshold as T11h (`edgar_source.py:1357-1377`) | A quarantined CIK's filings are missing, not mis-stamped; must never be read as "no facts" | **Yes** (allowance; universe eligibility) |
| P6 | form.idx row that does not parse | **Partly** | `edgar.py:254-266` (regexes), `:278-283` (raise) | **Q** the row (allowance) + **R** form and acceptance from the CIK's submissions by accession; **F** if no accession can be recovered | None if repaired from submissions (the same `known_at` source); repairing from the index `filed` date **would** break PIT and is not allowed | **Yes** (allowance; whether a 25/25-NSE row may ever be dropped) |
| P7 | Non-UTF-8 byte | **Handled** | `edgar_source.py:1869-1890`; `edgar.py:715-722`, `:754-757` | **R** in free text; **F** the accession if a kept field has U+FFFD (keep) | None (values never guessed) | No |
| P8 | Unquoted tabs | **Handled** (free-text columns); **fails** elsewhere | `edgar_source.py:1875-1906` | **R** free text only (keep); other members **F**, listed by the pre-flight | Folding a non-free-text column would shift fields: never | No |
| P9 | Schema or page drift | **Handled** | `edgar_raw.py:521-523`; `edgar_source.py:1820-1823`, `:1853-1862` | **F** (keep) + pre-flight | n/a | No |
| P10 | Truncated or corrupt zip/gz download | **Partly** (gz cache re-fetches; zips do not) | `edgar_source.py:391-394` (gz cache); `:461`, `:1623`, `:1817` (`ZipFile`, no catch); `edgar_raw.py:428-457` (no length check) | **T**: re-download once on `BadZipFile`, CRC or length mismatch, then **F** | None | No |
| P11 | Empty or truncated SGML `.txt` | **Handled** | `edgar.py:399-405`; `edgar_source.py:994-1004`, `:1195-1229` | **Q** (keep, T11h) | None | No |
| P12 | Acceptance-time zones | **Handled** | `edgar.py:120-123`, `:205-216`, `:406-409`, `:603-609`; `edgar_source.py:431-434` (unstampable) | **F**/unstamped; **never R** | **High**: any time repair (FSN `accepted`, companyfacts `filed`, index date) breaks `known_at` | No |
| P13 | Duplicate or conflicting values; amendments | **Handled** | `edgar.py:758-759`, `:784-785`, `:811-830`; `edgar_source.py:1500-1529`; date cap `:1595-1596` | **Q** the key (keep) | The date cap moves `as_of_date` earlier only: safe | No |
| P14 | Malformed ticker-map row | **Partly** (empty ticker or exchange skipped; any other bad row fails) | `edgar.py:299-322` | **Q** the row with a count (snapshot, current-only) | None (`known_at` = fetch time) | No |
| P15 | Form 25 quirks | **Handled** | `edgar_source.py:185-187`, `:1050-1140`; `edgar.py:570-598`; `store/delistings.py:20-23`, `:74-80`, `:175-176` | Keep | None | No |
| P16 | FSN re-issue; bulk files trailing the day | **Handled** | `edgar_source.py:598-622`, `:455-484`, `:1636-1649` | Keep (counted) | Re-extracting would rewrite past rows: keep "first extracted wins" | Already decided (#174 Q5) |
| — | Pre-flight sweep of every bulk and index input | **Not present** | (new; `backfill.py:129`, `:242` show one exception halts the run) | **F** before ingest, listing every bad record in one pass | None (read-only) | **Yes** (scope) |
| — | Rate setting at the ceiling | Config | `edgar_raw.py:188` (`requests_per_second`) | Consider 9 (E3) | None | Yes (follow-up only) |

## Sources
All seen on 2026-10-02.
- **S1** SEC, Webmaster FAQ (Developers; Data Resources): https://www.sec.gov/about/webmaster-frequently-asked-questions (Tier 1)
- **S2** SEC, Accessing EDGAR Data: https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data (Tier 1)
- **S3** SEC, New Rate Control Limits (2021-07-27): https://www.sec.gov/filergroup/announcements-old/new-rate-control-limits (Tier 1)
- **S4** SEC block page "Your Request Originates from an Undeclared Automated Tool", seen via a mirror: https://www.biopharmawatch.com/news/RPRX/sec-gov-your-request-originates-undeclared-automated-tool-20151201 (SEC text, Tier 1 content via a Tier 3 host; UNVERIFIED on sec.gov)
- **S5** secedgar `client.py`: https://github.com/sec-edgar/sec-edgar/blob/master/secedgar/client.py#L79-L103 (Tier 1 for its behaviour; lines approximate)
- **S6** edgartools SEC compliance docs: https://edgartools.readthedocs.io/en/latest/resources/sec-compliance/ (Tier 3)
- **S7** secedgar #280: https://github.com/sec-edgar/sec-edgar/issues/280 (Tier 3)
- **S8** edgartools `httprequests.py`: https://github.com/dgunning/edgartools/blob/main/edgar/httprequests.py#L96-L109 (Tier 1 for its behaviour; lines approximate)
- **S9** sec-edgar-downloader `_sec_gateway.py`: https://github.com/jadchaar/sec-edgar-downloader/blob/master/sec_edgar_downloader/_sec_gateway.py (Tier 1 for its behaviour)
- **S10** python-edgar `main.py`: https://github.com/edgarminers/python-edgar/blob/master/edgar/main.py#L66-L71 (Tier 1 for its behaviour; lines approximate)
- **S11** edgartools #1349: https://github.com/dgunning/edgartools/issues/1349 (Tier 3)
- **S12** edgartools #1351 and PR #1358: https://github.com/dgunning/edgartools/issues/1351 (Tier 3)
- **S13** edgartools #672: https://github.com/dgunning/edgartools/issues/672 (Tier 3)
- **S14** edgartools #674: https://github.com/dgunning/edgartools/issues/674 (Tier 3)
- **S15** secedgar #310: https://github.com/sec-edgar/sec-edgar/issues/310 (Tier 3)
- **S16** edgartools #443: https://github.com/dgunning/edgartools/issues/443 (Tier 3)
- **S17** SEC FS/FSN data-set documentation (search snippet only): https://www.sec.gov/files/aqfsn_1.pdf, https://www.sec.gov/files/financial-statement-data-sets.pdf (Tier 1; the PDF could not be rendered here)
- **S18** secfsdstools #16: https://github.com/HansjoergW/sec-fincancial-statement-data-set/issues/16 (Tier 3)
- **S19** secfsdstools #15: https://github.com/HansjoergW/sec-fincancial-statement-data-set/issues/15 (Tier 3)
- **S20** edgartools #557: https://github.com/dgunning/edgartools/issues/557 (Tier 3)
- **S21** edgartools #701, #769, #1295, #1196: https://github.com/dgunning/edgartools/issues/701, https://github.com/dgunning/edgartools/issues/769, https://github.com/dgunning/edgartools/issues/1295, https://github.com/dgunning/edgartools/issues/1196 (Tier 3)
- **S22** edgartools #1072: https://github.com/dgunning/edgartools/issues/1072 (Tier 3)
- **S23** edgartools #713: https://github.com/dgunning/edgartools/issues/713 (Tier 3)
- **S24** edgarjure changelog (form.idx is fixed-width; switched to `master.idx`): https://cljdoc.org/d/com.github.clojure-finance/edgarjure/0.2.0/doc/changelog (Tier 3, search summary only)
- **Local** issues and PRs #358, #455, #498, #554, #566, PR #567: https://github.com/josejuarez96/tradepartner/issues/358, /issues/455, /issues/498, /issues/554, /issues/566, /pull/567 (Tier 1, our own observations)
- **Local** code at `src/tradepartner/adapters/{edgar,edgar_raw,edgar_source}.py`, `store/delistings.py`, `ingest.py`, `backfill.py` (finch checkout, main at `10905e6`); fixtures `tests/fixtures/edgar/sgml_header_*.txt`, `submissions_*.json` (Tier 1)
- Builds on [2026-09-25-free-data-terms.md](2026-09-25-free-data-terms.md) and [2026-09-25-cover-page-facts-at-scale.md](2026-09-25-cover-page-facts-at-scale.md)
