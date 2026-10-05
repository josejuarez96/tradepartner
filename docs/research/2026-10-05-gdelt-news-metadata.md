# Research Report: GDELT as the v1 news-metadata source (terms, cadence, stamp semantics)

**Brief:** #882 (owner-approved 2026-10-05)  ·  **Date:** 2026-10-05  ·  **Status:** INCOMPLETE. The budget is spent (12 of 12 searches; 8 sources, plus 3 failed fetch attempts). The graded claim, the terms row, the cadence and the recommendation are done. Four sub-items are still unverified: the batch-label convention, the outage record, a Tier 1 definition of `seendate`, and a measured volume. See "What this report could not establish".  ·  **Agent/model:** researcher agent, claude-opus-5-5

## Answer
**Verdict on the brief's claim** ("GDELT's `<named time field>` is never earlier than public availability, with a bounded lag"): **INSUFFICIENT**  ·  **Confidence:** medium. The evidence that the claim is unproven is solid. The *direction* of the risk is inferred, not measured.

The time fields in GDELT's bulk products are all **processing-batch stamps**, not the article's stated publication time:
- Events `DATEADDED` is when the event "was added to the master database".
- Mentions `MentionTimeDate` is "the 15-minute timestamp … of the current update", "identical for all entries in the update file".
- GKG `DATE` "will be the same for all rows in a file".
- The GKG record id carries "the full date+time of the 15 minute update batch".

Sources: S2 p.6–7; S3. No Tier 1 source states that any of these stamps is never earlier than publication, and none bounds the lag from publication to GDELT's crawl. Two GDELT documents contradict their own field definitions by calling these stamps the publication time (S1 for Mentions, S3's own `DATE` prose). Whether a file's timestamp labels the start or the end of its 15-minute window is not documented. So a batch stamp could plausibly precede the article's appearance by up to one interval (**UNVERIFIED**, inference).

This does **not** block an honest collector. The event-data spec already sets `known_at` = our fetch time (spec req 2 (d)). A GDELT record cannot be fetched before GDELT publishes it, and GDELT cannot publish it before it has crawled the article. So the fetch-time stamp can never be earlier than public availability. That is a property of the collector, not of GDELT's documentation.

GDELT passes the terms gate cleanly: free, commercial use allowed, redistribution allowed with citation and link (S5). It does not offer a link from records to issuers that avoids a model: GKG organizations come from GDELT's own extraction engine (S3). It also cannot supply the spec's "source-stated publication time" field from its bulk files. It can supply GDELT's first-seen batch time, under that name.

## Answers to the brief, question by question

| Brief question | Answer | Basis |
|---|---|---|
| Terms of use and redistribution | Free; "unlimited and unrestricted use for any academic, commercial, or governmental use of any kind without fee"; redistribution, rehosting and mirroring allowed with "a citation to the GDELT Project and a link" | S5 (Tier 1) |
| Update cadence | Events, Mentions and GKG: every 15 minutes since 2015-02-19. "Within 15 minutes of GDELT monitoring a news report … it has translated it, processed it" | S1, S2 p.1 (Tier 1) |
| Which products carry headline, source, URL and time | **Events:** `SOURCEURL` (the first report found) plus `DATEADDED`. No headline, no source name. **Mentions:** `MentionSourceName`, `MentionIdentifier` (the URL) and `MentionTimeDate`, but only for articles that yielded a coded event. No headline. **GKG 2.1:** `V2SourceCommonName`, `V2DocumentIdentifier` (the URL) and `DATE`. A `<PAGE_TITLE>` in the extras XML is documented only in a search snippet of a GDELT blog post (**UNVERIFIED**). **DOC 2.0 API:** `url`, `title`, `seendate`, `domain`, `language`, `sourcecountry`, over a rolling 3-month window | S2, S3, S4 (Tier 1); PAGE_TITLE: snippet only |
| What each time field means | See the stamp table below | S1–S4 |
| Safe `known_at` upper-bound proxy, and its lag | **None of GDELT's fields is proven safe.** The only stamp that is safe by construction is our fetch time. Lag from publication to GDELT's batch: **not documented and not bounded**. Processing takes ≤15 minutes after GDELT "monitor[s]" an article (S1), but nothing documents how long the crawl takes to find it | S1, S2, S3 |
| Revisions, re-emission, backfill, late or replaced files | Mentions re-record old events: "a mention today of an event from a year ago will still be recorded" (S2). GKG 2.1 does not deduplicate: "20 articles … will appear as 20 separate entries" (S3). GDELT announced a historical backfile back to 1979 "in the GDELT 2.0 format" (S1). Late, missing or replaced 15-minute files: **not documented**. One Tier 3 package page says some intervals have missing data | S1–S3; Tier 3 snippet |
| Matching records to US-listed issuers without a model | **No source-provided link exists that avoids a model.** GKG organizations come from GDELT's extraction engine ("tuned to err on the side of inclusion", "the Leetaru (2012) algorithm", S3). They fail the spec's ADR 0008 rule. Event actor codes are machine-coded too. The only route without a model is our own deterministic rule: match company names or tickers against the title or URL, or run a DOC API keyword query per company. Its precision is unmeasured, and names that are common words (Apple, Target, Gap) will produce false positives | S3, S4, S6 |
| Rate limits | Bulk files: no documented limit. DOC API: 1 request per 5 seconds (HTTP 429 "Please limit requests to one every 5 seconds"). That figure is Tier 3 and reflects tooling status only. 250 records per query at most | S4 (Tier 1, max records); Tier 3 (rate) |
| Schema stability since 2015 | GKG: "The file format is now stabilized and will not change" (S3). Events 2.0 has been in this format since 2015-02-19, and GDELT 1.0 daily files ran on in parallel (S1, S2 p.1). The ONS (2020) notes the major 2015 change and that "a new version was expected in early 2020" (S7). No break since 2015 was found, but none was searched for file by file | S1–S3, S7 |
| Volume, top-1000 US universe | **Estimate only**, roughly 3,500–21,000 rows a day. The arithmetic is below. It rests on an unverified figure of ~700,000 articles a day | Snippet (**UNVERIFIED**) plus a stated assumption |
| One free alternative | Alpaca News API (Benzinga), evaluated below. It has headline, url, source, symbols, `created_at` and `updated_at`, and history "dating back to 2015" (S8). The meaning of the timestamps and the symbol-tagging method are undocumented in S8 | S8 (Tier 1); FT-1 |

### Stamp semantics, per product (the critical question)

| Product | Field | Documented meaning (quoted) | Processing time or publication time? | Safe as a `known_at` upper bound? |
|---|---|---|---|---|
| Events 2.0 | `DATEADDED` | "the date the event was added to the master database in YYYYMMDDHHMMSS format in the UTC timezone. For those needing to access events at 15 minute resolution, this is the field that should be used" (S2 p.6) | Processing (batch) | **Not proven.** Window-label convention undocumented |
| Events 2.0 | `Day` / `SQLDATE` | "Date the event took place" (S2) | Neither: the date the *event* happened, which can be long before any article | **Never.** A look-ahead risk if used |
| Events 2.0 | `SOURCEURL` | "the URL or citation of the first news report it found this event in" (S2) | First report GDELT *found*, which need not be the first published | n/a |
| Mentions | `MentionTimeDate` | "the 15-minute timestamp (YYYYMMDDHHMMSS) of the current update. This is identical for all entries in the update file" (S2 p.7) | Processing (batch). **Conflicts with** S1: "along with the timestamp the article was published" | **Not proven** |
| Mentions | `EventTimeDate` | "when the event being mentioned was first recorded by GDELT (the DATEADDED field of the original event record)" (S2 p.7) | Processing time of the *first* mention | **No.** Can be a year before this article (S2) |
| GKG 2.1 | `DATE` | "the date … on which the news media used to construct this GKG file was published … This date will be the same for all rows in a file" (S3) | Its prose says publication, but one value per file means it is in effect the batch time. **Self-contradictory** | **Not proven** |
| GKG 2.1 | `GKGRECORDID` prefix | "the full date+time of the 15 minute update batch that this record was created in" (S3) | Processing (batch) | **Not proven** |
| GKG 2.1 | `<PAGE_TITLE>` (extras) | Title of the article. Coverage starts "for all articles processed after noon EST" on an unstated date (snippet of GDELT blog p=3176, **UNVERIFIED**) | n/a | n/a |
| Any bulk file | File-name timestamp (e.g. `YYYYMMDDHHMMSS.export.CSV.zip`) | Not defined in S1–S3. **UNVERIFIED** whether it labels the window start or its end, and when the file is posted | Processing | **Not proven** |
| DOC 2.0 API | `seendate` | Not defined in S4. Tier 3 tooling describes it as "when GDELT first saw the article (in UTC)". S4 says `sort=DateDesc` sorts "by publication date" | Probably first-seen, i.e. processing (**UNVERIFIED** at Tier 1) | **Not proven** |
| Web News NGrams 3.0 / Global Frontpage Graph | n/a | NGrams 3.0: unigrams plus snippets from 2020, "updated every 15 minutes". GFG: 50,000 homepages scanned "every hour on the hour" (search snippets of GDELT blog posts, **UNVERIFIED**) | Scan time | Neither is a headline-record product. Not evaluated further |

**Why "never earlier than publication" fails for the batch stamps, and holds for our fetch time.** GDELT can only see an article after it is online, so GDELT's *first-seen instant* is at or after publication. A batch label, however, is one value for every article in the batch (S2, S3). If the label marks the window start, an article published and seen at 11:40 sits in a batch labelled 11:30. Its stamp would then be up to ~15 minutes *earlier* than the article existed. The label convention is undocumented, so this is a live possibility, not a finding. The lag in the other direction (late crawl, translation, re-mention, backfile) has no documented bound at all. Our own fetch time cannot precede GDELT's file, and the file cannot precede the article. The spec's rule (`known_at` = fetch, `stamp_resolution_minutes` stored) is therefore honest whichever way the label convention falls.

## Evidence
All pages were seen on 2026-10-05.

| # | Claim | Source | Tier | Key figure (quoted) | OOS / post-pub? | Net of costs? |
|---|---|---|---|---|---|---|
| 1 | 15-minute cadence; processing within 15 minutes of monitoring | S1 | 1 | "Within 15 minutes of GDELT monitoring a news report breaking anywhere the world, it has translated it, processed it" | n/a | n/a |
| 2 | Stream starts 2015-02-19; historical backfile announced | S1 | 1 | "only stretch back to late morning February 19, 2015"; "releasing the entire historical backfile back to 1979 in the GDELT 2.0 format" | n/a | n/a |
| 3 | Mentions "published" timestamp (**conflicts with 5**) | S1 | 1 | "records every mention of an event over time, along with the timestamp the article was published" | n/a | n/a |
| 4 | `DATEADDED` is the time added to the database, at 15-minute resolution | S2 p.6 | 1 | "the date the event was added to the master database … UTC" | n/a | n/a |
| 5 | `MentionTimeDate` is the batch time, the same for the whole file | S2 p.7 | 1 | "the 15-minute timestamp (YYYYMMDDHHMMSS) of the current update. This is identical for all entries in the update file" | n/a | n/a |
| 6 | Old events are re-mentioned | S2 | 1 | "a mention today of an event from a year ago will still be recorded" | n/a | n/a |
| 7 | GKG `DATE` is called publication time, yet is one value per file | S3 | 1 | "the date of publication … This date will be the same for all rows in a file" | n/a | n/a |
| 8 | GKG record id carries the batch time | S3 | 1 | "the full date+time of the 15 minute update batch that this record was created in" | n/a | n/a |
| 9 | GKG 2.1 does not deduplicate | S3 | 1 | "if 20 articles all contain the same list of … they will appear as 20 separate entries" | n/a | n/a |
| 10 | GKG organizations come from GDELT's own extraction engine and favour recall | S3 | 1 | "tuned to err on the side of inclusion when it is less confident about a match" | n/a | n/a |
| 11 | GKG format frozen | S3 | 1 | "the file format is now stabilized and will not change" | n/a | n/a |
| 12 | DOC API: 3-month window, 250 records per query, title and seendate | S4 (2017-06-20) | 1 | "rolling window of the last 3 months"; "up to 250 results"; ArtList fields url, title, seendate, domain, language, sourcecountry | n/a | n/a |
| 13 | Terms | S5 (page © 2013–2022) | 1 | "unlimited and unrestricted use for any academic, commercial, or governmental use of any kind without fee"; "must include a citation to the GDELT Project and a link" | n/a | n/a |
| 14 | Coding validity is low and events duplicate | S6, Science 353(6307):1502–1503 (2016) | 1 | "only 21% of GDELT's valid URLs indicate a true protest event"; "computer-automated event data often duplicate and misclassify events" | n/a | n/a |
| 15 | No known quality-assurance mechanism | S7 (2020-01-09) | 2 | "It is unclear whether any specific quality assurance mechanisms are built into the GDELT data collection and processing process" | n/a | n/a |
| 16 | Alternative: Alpaca News is Benzinga data since 2015 | S8 (updated 2025-09-24) | 1 | "All news data is currently provided directly by Benzinga"; "dating back to 2015" | n/a | n/a |

## Disconfirmation
- **Searches run** (all recorded in the search log): MentionTimeDate/DATEADDED definitions (#1); DOC API `seendate` and the rate limit (#2, #9); missing or late 15-minute files and outages (#3, #6); academic data-quality critiques (#4); measured lag from publication to GDELT (#7); terms changes 2024–2026 (#10); GDELT 3 products (#11); volume (#12).
- **Time fields predate or lag publication.** Found against the claim, in the form of contradictions. S1 and S3's `DATE` prose call batch stamps "published" times. S2 and S3's own same-for-every-row sentences show they are batch times. No study measuring the lag from publication to GDELT was found (#7 returned studies of news diffusion, not indexing latency). **Lag: unbounded in the record.** Re-mentions (S2) and the 1979 backfile (S1) show that GDELT stamps can come long after publication. That direction is harmless under a fetch-time `known_at`. The window-label convention, the possible "earlier" case, could not be checked.
- **Outages and gaps.** No Tier 1 outage record was found (#3, #6 returned unrelated outage pages and a GDELT Analysis email-service post). One Tier 3 package listing (pypi `gdelt`) says some intervals have missing data. **Treat gaps as likely and unquantified.** The spec's gap-day card (req 7) is the planned detector.
- **Terms changes.** None found for the GDELT Project (#10). A separate commercial service, "GDELT Cloud" (gdeltcloud.com), has its own terms, "last updated June 6, 2026" per a snippet. Its affiliation with the GDELT Project is **UNVERIFIED**. Its terms do not govern the free datasets under S5.
- **Deduplication problems.** Found. GKG 2.1 keeps every copy of a syndicated story as its own row (S3). Science 2016 reports GDELT "often duplicate[s] and misclassif[ies] events", with 21% validity on protest URLs (S6). Counts of news rows per issuer will therefore be inflated by syndication unless we deduplicate by URL or by title.
- **Academic critiques bearing on headline metadata.** S6 and S7 are about event and actor coding, not URLs or titles. They bear on metadata only by showing that GDELT's computed fields are unreliable, which supports the spec's exclusion of source-computed fields.

## Volume estimate (rows per day, top-1000 US universe)
- Basis: GKG "growing by nearly 700,000 articles a day". This is a search-result summary attributed to a GDELT blog page, and the year is unknown (**UNVERIFIED**).
- Per 15-minute file: 700,000 / 96 ≈ **7,300 rows**.
- Share of articles whose title or URL names a top-1000 US-listed company: **assumed** 0.5%–3%. No source; this is a guess to be replaced by measurement.
- Rows a day: 700,000 × 0.005 ≈ **3,500** to 700,000 × 0.03 ≈ **21,000**, before deduplication by URL or title. Syndication (S3) inflates the upper end.
- DOC API route instead: 1,000 companies × 1 query × 5 s = 5,000 s ≈ **83 minutes per sweep**. That exceeds a 15-minute or 60-minute `collect.interval_minutes`, so `stamp_resolution_minutes` would be ≥90. Each query is capped at 250 records (S4). The 5 s figure is Tier 3.
- Raw cache: keeping whole GKG files as the raw record (spec req 5) at 96 files a day would likely exceed the provisional 1 GB news budget within days. File sizes are **UNVERIFIED**, so this needs measuring. The spec already schedules a one-week forward measurement.

## Alternative evaluated: Alpaca News API (Benzinga)
- **Gates passed:** a free account; the same keys as the existing price adapter; history since 2015 (S8). Fields include `headline`, `url`, `source`, `symbols`, `created_at`, `updated_at` (S8). Rate is 200 calls a minute on the free plan (Tier 3 snippet; consistent with FT-4's Basic limit). Many symbols fit in one call (Tier 3 snippet). A 15-minute poll of 1,000 names is feasible.
- **Gates open:** S8 does not define `created_at` or `updated_at`. `updated_at` implies records are revised after publication, so as-fetched storage is mandatory. How Benzinga assigns `symbols` (editors, rules or a model) is not documented, so ADR 0008 compliance is unknown. Terms are Alpaca's personal, non-commercial terms (FT-1), and Benzinga's content licence via Alpaca was not read. The payload carries `summary` and `content`, which is full text. The spec's "never full article text" would need the content excluded at request or parse, and whether the raw cache may hold it is a spec question.
- **Comparison:** better than GDELT on issuer linking (a symbols field exists) and on fit to the cadence. Worse on terms (personal, non-commercial, no redistribution versus GDELT's open terms) and on documentation of timestamps and tagging.

## What the evidence supports for the owner's decision (the brief asks for a recommendation; the owner decides)
- **Collect from GDELT:** passes on terms (S5) and on cadence (S1). It also passes on stamp honesty, but only because the spec stamps fetch time. Unresolved: issuer linking without a model (our own name-matching rule, precision unmeasured), headline coverage in GKG (`PAGE_TITLE` is snippet-level only), raw-cache size, and volume. Use GKG 2.1 15-minute files, not Events/Mentions (no headline, and coded events are the low-validity part, S6), and not the DOC API (83-minute sweeps). Store GDELT's batch time as `gdelt_batch_time`, **not** as "source-stated publication time". GDELT does not supply the latter.
- **Collect from Alpaca News instead:** better issuer linking and cadence, but timestamp semantics, symbol-tagging method and content licensing are undocumented. Two of these bear directly on the spec's ADR 0008 rule and its no-full-text rule.
- **Do not collect in v1:** supported if the owner treats "issuer link without a model, documented at Tier 1" as a hard gate. Neither source passes it.
- **Lean of the evidence:** *collect from GDELT, low confidence, conditional on the spec's one-week forward measurement confirming three things*: (1) `PAGE_TITLE` is present on most rows; (2) a deterministic name or ticker rule reaches acceptable precision on a hand-checked sample; (3) the raw-cache size fits a budget the owner accepts. If any of the three fails, the evidence supports dropping (d) from v1 rather than switching to Alpaca News, until Alpaca's timestamp and tagging semantics are documented. The point-in-time argument for starting early (spec, Problem) is the owner's to weigh. This report grades only the evidence.

## Caveats & gaps
- Both codebooks were read as text renderings through r.jina.ai, because direct PDF fetches failed. Page numbers come from the rendering (S2) or are absent (S3).
- S1 and S3 contradict S2 on what the time stamps mean. Per the protocol both stay. The same-for-every-row sentences are the stronger evidence, since a per-file constant cannot be a per-article publication time.
- GDELT's terms cover GDELT's datasets. Headlines and URLs belong to third-party publishers, and S5 cannot license them. For personal, private storage this is unlikely to matter, but it was not researched (not legal advice).
- The volume estimate rests on one unverified figure and one assumed share.
- The DOC API rate limit is Tier 3. It is used as tooling status only.

## UNVERIFIED items
- Whether a bulk file's timestamp labels its window start or end, and when the file is posted relative to that label.
- Whether a 15-minute file can be posted late, replaced, or never posted, and how often.
- `<PAGE_TITLE>` in the GKG extras: its existence, its start date and its coverage share (snippet of blog p=3176 only).
- The meaning of DOC API `seendate` (Tier 3 only).
- The DOC API rate of 1 request per 5 seconds (Tier 3 only).
- ~700,000 GKG articles a day (snippet, year unknown).
- GDELT Cloud's affiliation with the GDELT Project, and the relevance of its 2026-06-06 terms.
- Alpaca News: `created_at`/`updated_at` semantics, the symbol-tagging method, free-plan news rate, and Benzinga licensing.
- Web News NGrams 3.0 and Global Frontpage Graph details (snippets only).

## What this report could not establish
1. A bounded lag between article publication and any GDELT stamp. No Tier 1 or Tier 2 measurement was found.
2. Whether any GDELT batch stamp can be earlier than the article's publication. This depends on the undocumented window-label convention.
3. An outage or gap record for the 15-minute stream since 2015.
4. Whether headline coverage in GKG 2.1 is complete enough to serve as "headline-level records".
5. The precision of any deterministic issuer-matching rule. This needs a hand-checked sample, which a probe could produce and a document search cannot.
6. Real volume and raw-file sizes. These need the spec's one-week forward measurement.

## Follow-up questions (not answered here)
- Should the spec's (d) field "source-stated publication time" be renamed for GDELT (e.g. `source_first_seen_batch`), since GDELT's bulk files carry no publisher-stated time? (This is a spec edit, class B.)
- Should the raw-cache rule (req 5) let a news adapter keep only matched rows plus file hashes, rather than whole GKG files? The trade-off is reparse power against disk.
- Deduplicating syndicated copies (by URL or title) at read time versus at collect time.
- Do the Alpaca and Benzinga terms allow private storage of headlines, and can `content` be excluded at request time?

## Search log
Budget: 8 sources / 12 searches. Used: **12 searches, 8 sources** (S1–S8), plus **3 failed fetch attempts** (2 direct PDFs and 1 Scribd page), whose content was then obtained via r.jina.ai for the same documents. Over budget by 3 fetch calls but not by any source. The reason was PDF extraction failure. Search-result snippets cited as UNVERIFIED are not counted as sources.

| # | Kind | Query / URL | Result |
|---|---|---|---|
| F1 | fetch | data.gdeltproject.org/documentation/GDELT-Event_Codebook-V2.0.pdf | Failed: binary PDF not parsed |
| S#1 | search | GDELT codebook "MentionTimeDate" "DATEADDED" … | Tier 3 summaries; pointed to the codebook |
| F2 | fetch | scribd.com copy of the Event codebook | Failed: metadata only |
| F3 | fetch | r.jina.ai/…GDELT-Event_Codebook-V2.0.pdf (2 prompts) | **S2** |
| F4 | fetch | blog.gdeltproject.org/gdelt-2-0-our-global-world-in-realtime/ | **S1** |
| F5 | fetch | r.jina.ai/…GDELT-Global_Knowledge_Graph_Codebook-V2.1.pdf | **S3** |
| F6 | fetch | blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/ | **S4** |
| F7 | fetch | gdeltproject.org/about.html | **S5** |
| S#2 | search | GDELT DOC API "seendate" … "one every 5 seconds" | Tier 3: seendate = first seen; 429 at 1 request per 5 s |
| S#3 | search | GDELT 2.0 missing 15-minute update files gap outage masterfilelist | Tier 3: "some intervals can have missing data"; no Tier 1 |
| S#4 | search | GDELT data quality duplicate events critique … | Found S6, S7 |
| F8 | fetch | people.cs.vt.edu/~naren/papers/1502.full.pdf | Failed: binary PDF |
| F9 | fetch | r.jina.ai/ same | **S6** |
| F10 | fetch | ons.gov.uk GDELT data quality note | **S7** |
| F11 | fetch | docs.alpaca.markets/docs/historical-news-data | **S8** |
| S#5 | search | GKG "PAGE_TITLE" extras | Snippet of blog p=3176: PAGE_TITLE added (UNVERIFIED) |
| S#6 | search | blog outage "15 minute" updates delayed … | Nothing relevant (disconfirmation: no record) |
| S#7 | search | GDELT latency publication to appearance, measured | No indexing-latency study found |
| S#8 | search | Alpaca news API Benzinga free plan … | Tier 3: 200/min free, since 2015 |
| S#9 | search | site:blog.gdeltproject.org "seendate" DOC API | Tier 3 only |
| S#10 | search | GDELT terms of use change … 2024–2026 | No change found; GDELT Cloud terms 2026-06-06 (separate service) |
| S#11 | search | Global Frontpage Graph / Web News NGrams 3.0 | Snippets: GFG hourly, 50,000 homepages; NGrams 3.0 from 2020, 15-minute updates |
| S#12 | search | GDELT GKG articles per day volume | Snippet: ~700,000 articles a day (UNVERIFIED) |

## Sources
All were seen on 2026-10-05.
- **S1** GDELT Project blog, "GDELT 2.0: Our Global World in Realtime" (2015-02-19): https://blog.gdeltproject.org/gdelt-2-0-our-global-world-in-realtime/ (Tier 1)
- **S2** GDELT Event Codebook V2.0 (Leetaru), pages 1, 6, 7: http://data.gdeltproject.org/documentation/GDELT-Event_Codebook-V2.0.pdf, read via https://r.jina.ai/ (Tier 1)
- **S3** GDELT Global Knowledge Graph Codebook V2.1: http://data.gdeltproject.org/documentation/GDELT-Global_Knowledge_Graph_Codebook-V2.1.pdf, read via https://r.jina.ai/ (Tier 1)
- **S4** GDELT Project blog, "GDELT DOC 2.0 API Debuts" (2017-06-20): https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/ (Tier 1)
- **S5** GDELT Project, About / Terms of Use: https://www.gdeltproject.org/about.html (Tier 1)
- **S6** Wang, Kennedy, Lazer, Ramakrishnan, "Growing pains for global monitoring of societal events", *Science* 353(6307):1502–1503 (2016): https://people.cs.vt.edu/~naren/papers/1502.full.pdf (Tier 1)
- **S7** UK Office for National Statistics, "GDELT data quality note" (2020-01-09): https://www.ons.gov.uk/peoplepopulationandcommunity/birthsdeathsandmarriages/deaths/methodologies/globaldatabaseofeventslanguageandtonegdeltdataqualitynote (Tier 2: institutional assessment)
- **S8** Alpaca, Historical News Data (updated 2025-09-24): https://docs.alpaca.markets/docs/historical-news-data (Tier 1)
- Snippet-only, not counted, used only where marked UNVERIFIED: GDELT blog p=3176 "New GKG 2.0 Article Metadata Fields" https://blog.gdeltproject.org/?p=3176 ; pypi `gdelt` https://pypi.org/project/gdelt/ (Tier 3) ; GDELT Cloud terms https://www.gdeltcloud.com/terms ; Alpaca News API blog https://alpaca.markets/blog/introducing-news-api-for-real-time-fiancial-news/ (Tier 3) ; GDELT blog, Web News NGrams 3.0 https://blog.gdeltproject.org/announcing-the-new-web-news-ngrams-3-0-dataset/ ; GDELT blog, Global Frontpage Graph https://blog.gdeltproject.org/the-gdelt-global-frontpage-graph-gfg-134-billion-urls-and-three-quarters-of-a-trillion-datapoints/amp/
