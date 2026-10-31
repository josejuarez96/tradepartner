# 0028. One ingest standard: keep raw downloads, parse from them, declare the plumbing

**Status:** Proposed  ·  **Date:** 2026-10-31  ·  **Issue:** #2712  ·  **Related:** [#2681](https://github.com/josejuarez96/tradepartner/issues/2681), [#2685](https://github.com/josejuarez96/tradepartner/issues/2685), [#2691](https://github.com/josejuarez96/tradepartner/issues/2691), [#2697](https://github.com/josejuarez96/tradepartner/issues/2697), [#2700](https://github.com/josejuarez96/tradepartner/issues/2700), [#2706](https://github.com/josejuarez96/tradepartner/issues/2706), T317–T324 ([data-foundation plan](../plans/data-foundation.md)), [ADR 0009](0009-price-vendor.md) (the $0 budget), [ADR 0023](0023-break-first-fast-books-paper-exam.md) (the schema freeze)

## Context

In plain words: each data source has grown its own ingest path. Most of the cost is in the plumbing around the parser, not in the parser.

Each source answers the same five questions in its own code:
- what to fetch;
- how to skip what is already held;
- how to compare two sources for the same fact;
- how to record a correction;
- how to fix a stored mistake.

One day of Form 4 work (2026-10-31) hit all five:

| Problem (2026-10-31) | Shared question |
|---|---|
| A rerun scanned one table per filing for hours (#2681), then re-stamped filings it already held (#2685, #2688) | Skipping what is held |
| 11 whole quarters refused because one sampled filing disagreed (#2691, #2706) | Comparing two sources |
| SEC's bulk file pins roles on the wrong owner in some multi-owner filings (#2700) | Comparing two sources, choosing the trusted one |
| A bulk-parser bug left 30,439 stored rows wrong (#2697) | Fixing a stored mistake |
| Each investigation had to re-download Form 4 XML, because `edgar_raw.filing_xml` (edgar_raw.py:575) returns the text and writes nothing | Keeping the raw input |

Some sources already keep their raw input. `du` of 2026-10-31 shows `data/edgar_cache` at 8.0 GB:

| Cache | Size |
|---|---|
| bulk (submissions and companyfacts zips) | 2.8 GB |
| stamps | 1.6 GB |
| corpus | 1.1 GB |
| statement_facts | 739 MB |
| fsn | 595 MB |
| index | 423 MB |
| insider (Form 3/4/5 data sets) | 416 MB |
| facts | 190 MB |
| header | 184 MB |
| delisting | 65 MB |

Form 4 XML and the daily index files are not kept. The store (`data/tradepartner.duckdb`, 2.0 GB) is written by unattended paper runs. Disk is the binding constraint: about 13 GB free on 2026-10-31, against a 10 GB floor.

The roles fix shows how far raw input alone gets us. Its raw input (the bulk zips) was already kept, and `repair-form4-roles` re-derives from it, hash-checked. A census, a manifest and a release were still needed: they come from the never-true rule (spec #1682 item 7, the #1613 precedent), not from missing raw files. A standard pipeline replaces the hand-written repair command. It does not replace that discipline.

Each format's parser is unavoidable. The question is what around it can be standard.

## Options considered

1. **Keep going source by source (today).** No new work. Every new source and every bug repeats the plumbing, and investigations keep re-downloading.
2. **Narrow fix: keep Form 4 XML and the daily index raw, nothing shared.** Fixes the re-download problem for the two sources hurting now, at the lowest cost. Each later source still invents its own skip, compare and repair code.
3. **A shared standard, adopted one source at a time (recommended).** Five rules plus small shared helpers. The first concrete step is option 2's scope, built to the standard, so the first cost is about the same as option 2. Later sources adopt the standard when they are next touched. There is no rewrite.
4. **A generic framework with a rewrite of every source.** One design at once, but a large project that pauses strategy work, and the owner capped infrastructure at about two teams (2026-10-30). Rejected.

Storage options for raw archives and backups:
- **(a) Laptop disk:** $0, but it is the scarce resource.
- **(b) External SSD:** about $60 once (whether a one-off hardware buy counts under ADR 0009's running-system budget is the owner's call), but a second device, and unusable for the live store.
- **(c) Google Drive for desktop, streaming mode:** the free 15 GB tier fits the first step; above that, about $2/month. It brings sync caveats (below).
- **(d) A scriptable object store (Backblaze B2, S3):** about $6 per TB per month and easy to script, but a new account and more setup.

## Decision

We will hold every source to five rules, adopting them one source at a time:

1. **Raw first.** A fetched response is kept before it is parsed, named by its content hash, with its URL and fetch time in the run row. Identical content (same hash) is not stored twice. **Nightly full snapshots are the exception:** `submissions.zip`, `companyfacts.zip` and quarterly `form.idx` are rebuilt by SEC in place. Until the owner chooses storage for them (question 2), these keep today's single latest copy. Weekly copies (the latest plus four, about 14 GB for the bulk zips) start only on an SSD, object storage or a paid tier, never on the laptop or the free tier. At about 2.8 GB per snapshot copy, keeping every night would pass 100 GB in about five weeks.
2. **Parse only from kept raw, and record where each row came from.** Parsing is a pure function of the raw file. Each run row names the raw file hashes it parsed and the parser version, as `edgar_index_files` already does for index files. A stored row maps to its run by an `ingested_at` within the run's start and finish (some write paths stamp at commit, not at start). The re-derive's manifest names every row, and a row matching no run fails the dry run. Recording this per run, not per row, needs no new table column.
3. **Two kinds of correction, never mixed:**
   - **(a) The source published a new value later**, as SEC does with `filed_on` (#2659). This is a new revision whose `known_at` is the run that first saw it. The old value stays visible as-of.
   - **(b) Our parser misread a payload that was already public.** The stored row was never true, so the re-derived row keeps its normal `known_at` (for example acceptance). It replaces the old row under a named release, with a reviewed manifest, stale trials and the open-window rule (spec #1682 item 7, the #1613 rule). A re-derive replaces only the hand-written repair command.
   - Tables that have no revisions by spec keep that exception: first-vintage `statement_facts`, `filing_events`, and `filing_index` until #2709.
4. **One change check per source.** A recorded fingerprint or cursor (ETag, zip hash, last accession, daily-file date) decides what is new. The aim is that a rerun costs only new work.
5. **A declared source-comparison rule.** When two sources carry the same fact, a per-source config entry names, for each field, which source is trusted and which differences are tolerated. Disagreeing rows are recorded and counted. A per-source threshold share of disagreeing rows fails the run, and `health` names it. This replaces the current all-or-nothing order sample, which failed whole quarters.

**Look-ahead guard.** Only ingest and re-derive open the raw archive. No backtest, universe, signal or research read opens it, enforced by a source scan as for `index_members.csv`. A re-derive reads only the raw files its run rows name, by hash. A later fetch is a new run with its own run row; a row it produces keeps `known_at` no earlier than that fetch's time.

**Storage.** The live store never lives on a synced or external disk, because an interrupted write by an unattended run could corrupt it. Sharadar data is never copied off the laptop. If the owner chooses (c) or (d) for the archive:
- **Integrity.** Raw files are content-addressed, so a sync conflict copy ("name (1)") is never matched. Every read re-checks the hash and refuses on a mismatch.
- **Offline.** The daily ingest never reads the archive. A re-derive whose raw file is unavailable fails loudly. It never falls back to a fresh SEC fetch, which would be later, different content.
- **Disk.** Drive for desktop keeps a local cache on the boot disk and may offer no size cap, so a re-derive checks the 10 GB floor before it starts and stops if it falls below.
- **Upload.** Raw files are written locally, verified by hash once uploaded, and only then removed locally.
- **Backups.** A backup is written outside the synced folder as a consistent snapshot, then moved in, so a half-written file is never uploaded. Backups hold broker order and account rows. Off the laptop they are encrypted before upload; this is the owner's call (question 4).
- **Paths.** The Drive path (`~/Library/CloudStorage/GoogleDrive-<account>`) contains the account email, so it lives in `.env` or local config, never in a committed default.

**First step, if accepted.** Option 2's scope, built to the standard:
- keep Form 4 XML and the daily index files raw;
- move the Form 4 skip logic onto rule 4;
- replace the order sample with rule 5's counted comparison, which also settles #2706's refused quarters.

Volume:
- Form 4 XML averages about 6 KB per filing (37 sampled on 2026-10-31). The ongoing archive is about 70,000 filings a quarter, roughly 0.4 GB raw, much less compressed per quarter.
- A back-fetch of all ~4.8 million historical Form 4 filings is about 29 GB raw and about a week at SEC's rate limit. It is **not** part of this step: we keep what we fetch from now on.
- Daily index files are about 1 MB a day.

**How this meets T317–T324.** T317–T324 ship as planned, with these edits:
- **T319a** keeps a differing quarterly `form.idx` under rule 1's snapshot exception instead of overwriting it.
- **The daily index files are kept raw.** This gives #2709's rebuild its source for corrected `filed_on`.
- **No parser-version column lands in T318.** It goes in the run row.
- T325 stays struck.

## Consequences

- **Good.**
  - Investigations stop re-downloading.
  - A re-derive replaces a hand-written repair command. The census, manifest, release, trial staling and open-window rule still apply.
  - Systematic disagreements between sources fail the run by a declared threshold, instead of whole quarters failing on one sample.
  - Over time, a new source should need mainly a parser and a config entry.
- **Bad / accepted risks.**
  - Migration effort, paid one source at a time.
  - If the owner chooses weekly snapshot copies, they take about 14 GB, which is why they are not kept nightly or by default. Raw Form 4 adds about 1.6 GB a year before compression.
  - Storage choice (c) above 15 GB, or (d), is running-system spend. That **amends ADR 0009's $0/month budget** and needs the owner's word. The free tier, or (a)/(b), keeps $0.
  - A never-true row parsed from a snapshot copy that has since rotated out cannot be re-derived; the re-derive fails loudly, and such a row still needs a hand-written repair.
  - Recording raw hashes in run rows is additive. It touches no fact-table schema under the ADR 0023 freeze.
  - Bars and corporate actions come from Alpaca. Its data terms are checked before that source adopts rule 1. The archive is not a cache of public data for every source.
- **Reversibility.** Cheap. The archive location is one config path, and sources not yet adopted are unaffected.
- **Revisit if:**
  - Drive streaming proves unreliable for reads.
  - Weekly snapshot copies outgrow the chosen storage.
  - A comparison threshold fires on a source we trusted.

## Open questions

1. Accept option 3, with the first step above? Recommended: yes.
2. Storage for the raw archive: laptop now and decide later; external SSD; Drive (free tier first); or B2/S3? Recommended: Drive free tier for raw Form 4 and daily index files. It keeps $0, and we revisit if it outgrows 15 GB.
3. Run the merged `repair-form4-roles` release (#2705) now, or wait for this standard? Recommended: now. It is built and audited, and the Form 4 study waits on it. The first step then uses that fix as its regression test.
4. Store backups off the laptop: yes, encrypted, or keep them local only? Recommended: yes, encrypted.
5. For #2700 (SEC's bulk file mispins owner roles in some multi-owner filings): should rule 5 trust the XML over the bulk file for multi-owner filings? Recommended: yes. This is a data call.
