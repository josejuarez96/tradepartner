# 0028. One ingest pipeline: keep raw downloads, parse from them, share the plumbing

**Status:** Proposed  ·  **Date:** 2026-10-31  ·  **Issue:** #2712  ·  **Related:** [#2681](https://github.com/josejuarez96/tradepartner/issues/2681), [#2685](https://github.com/josejuarez96/tradepartner/issues/2685), [#2691](https://github.com/josejuarez96/tradepartner/issues/2691), [#2697](https://github.com/josejuarez96/tradepartner/issues/2697), [#2700](https://github.com/josejuarez96/tradepartner/issues/2700), [#2706](https://github.com/josejuarez96/tradepartner/issues/2706), T317–T325 ([data-foundation plan](../plans/data-foundation.md))

## Context

In plain words: every data source has grown its own ingest path, and most of the cost is in the plumbing around the parser, not in the parser itself.

Each source (bars, corporate actions, the SEC filing index, XBRL facts, Form 4 XML, the Form 3/4/5 bulk data set) has its own answer to five questions: what to fetch, how to skip what is already held, how to compare two sources for the same fact, how to record a correction, and how to fix a stored mistake. One day of Form 4 work (2026-10-31) hit all five:

| Problem (2026-10-31) | Which shared question it is |
|---|---|
| A rerun scanned one table per filing for hours (#2681), then re-stamped 1.4M filings it already held (#2685, #2688) | Skipping what is already held |
| 11 whole quarters refused because one sampled filing disagreed (#2691, #2706) | Comparing two sources |
| SEC's bulk file pins roles on the wrong owner in multi-owner filings (#2700) | Comparing two sources, choosing the trusted one |
| A parser bug left 30,439 stored rows wrong; fixing them needed a census, a new repair command (#2702) and an owner release (#2705) | Fixing a stored mistake |
| Each investigation had to re-download filings, because Form 4 XML responses are not kept (`edgar_raw.filing_xml` writes nothing) | Keeping the raw input |

Some sources already keep their raw input: `data/edgar_cache/` holds the bulk zips (2.8 GB), the filing index (423 MB), the Form 3/4/5 data sets (416 MB), stamps, the FSN and statement-fact files and the corpus, about 8 GB in all. Others do not. The store is local DuckDB (`data/tradepartner.duckdb`, about 2 GB) written by unattended paper runs. Disk is the binding constraint: about 13 GB free on 2026-10-31 after cleanup, against a 10 GB floor.

The parser for each format is irreducible: Form 4 XML, the bulk TSVs, `form.idx` and XBRL are different formats. The question is whether everything around the parser can be shared.

## Options considered

1. **Keep going source by source (today).** No migration cost. Every new source and every bug repeats the plumbing; a parser fix keeps needing a census, a repair command and a release.
2. **Full rewrite into a generic framework.** One design at once. A large, risky project that pauses strategy work, and the owner capped infrastructure at about two teams (2026-10-30). Rejected.
3. **One shared pipeline, adopted source by source (recommended).** Five shared pieces, each source plugging in a parser and a short config entry. Form 4 moves first because it is where today's cost is, then the SEC filing index (T317–T325 already lean this way), then others only when touched.

## Decision (proposed)

We will ingest every source through one pipeline with five shared parts, adopting it one source at a time:

1. **Raw first.** Every downloaded response is kept unchanged, with its URL, fetch time and content hash, before anything is parsed. Raw files are immutable; a re-fetch that differs is a new raw file, not an overwrite.
2. **Parse from raw only, stamped with a parser version.** Parsing is a pure function of the raw file. Each stored row records the parser version that produced it. A parser fix is applied by re-deriving the affected rows from raw, with no network, as a normal scoped release.
3. **One correction rule.** A changed value is a new dated revision (its own `known_at`), never an overwrite, read through the existing `_latest_as_of` pattern. This generalises the owner's 2026-10-31 answer for `filed_on` (#2659).
4. **One change check per source.** A per-source fingerprint or cursor (ETag, zip hash, last accession) decides what is new, so a rerun costs only new work.
5. **One source-comparison rule.** When two sources carry the same fact, the comparison is declared per field: which source is trusted, which differences are tolerated, and what happens on disagreement. Disagreements are recorded and counted row by row; they never silently fail or pass a whole quarter.

**Storage.** Raw archives are packed into one file per source per day or per quarter, not one file per filing, so the count stays in the thousands. They live in a Google Drive folder synced by Google Drive for desktop in streaming mode: the pipeline reads a normal path, and a file downloads only when opened. Recent raw files the daily ingest uses stay on the laptop. Store backups go to the same Drive. **The live store never lives on Drive or any synced or external disk**, because an interrupted write by an unattended run could corrupt it. Licensed Sharadar data is never copied there.

**First step if accepted.** A spec amendment and plan with Form 4 as the pilot: keep Form 4 XML raw, re-derive the roles-2691 correction from raw in place of `repair-form4-roles`, and settle #2700's multi-owner question through the source-comparison rule (prefer XML for multi-owner filings).

## Consequences

- **Good.**
  - Parser bugs become re-derives, not repair commands plus releases.
  - Investigations need no re-downloads.
  - Reruns cost only new work.
  - Disagreements between sources are counted instead of failing whole quarters.
  - A new source is a parser plus config.
- **Bad / accepted risks.**
  - Migration effort, paid one source at a time.
  - Cloud storage: about USD 2/month (Google One 100 GB). Re-derives read from Drive are slower than local reads.
  - The archive is a cache of public SEC data, not irreplaceable. Losing it costs re-download time, not data.
  - Two storage locations to explain in the runbooks.
- **Reversibility.** Cheap. Raw archives can move to a local SSD or another store by changing one path; sources not yet migrated are unaffected.
- **Revisit if:**
  - Drive streaming proves unreliable for reads.
  - Raw volume outgrows 100 GB.
  - A source's terms forbid keeping its raw responses.

## Owner questions

1. Accept option 3, with Form 4 as the pilot?
2. Google Drive (streaming) for the raw archive and backups, or a scriptable store such as Backblaze B2 or S3 at similar cost?
3. For roles-2691 (#2705): wait for the raw-first Form 4 pilot and re-derive, or run the merged `repair-form4-roles` release now, before the Form 4 study? The orchestrator recommends running it now: it is built, audited and the study waits on it.
