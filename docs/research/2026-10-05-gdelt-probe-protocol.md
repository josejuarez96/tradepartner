# GDELT forward-measurement probe: protocol and pre-registered thresholds

**Brief:** #882 (owner decision of 2026-10-05: approve the one-week forward measurement gate)  ·  **Date:** 2026-10-05  ·  **Status:** DRAFT until the owner approves the thresholds below. Once approved, the numbers, the matching rule (`RULE_VERSION`), the sample size and the seed are **frozen before the first poll of the week**. Any later change is a new protocol version and restarts the week.  ·  **Branch:** `spike/882-gdelt-probe` (spike code, never merged)  ·  **Agent/model:** team gdeltprobe, claude-opus-5-5

**Inputs:** [GDELT report](2026-10-05-gdelt-news-metadata.md) (its recommendation section and "What this report could not establish" 4–6), [event-data spec](../specs/event-data.md) req 2 (d), req 5 and req 7, [ADR 0008](../decisions/0008-llm-role.md), [free-data terms](2026-09-25-free-data-terms.md) rows 9a–9c.

## 1. What the week decides

The owner's gate (#882): news collector (d) stays in v1 only if one forward week of GDELT GKG 2.1 files shows (1) the page title is present on most rows, (2) one fixed, deterministic name or ticker rule over the title and URL is precise enough on a hand-checked sample, and (3) the raw cache fits a budget the owner accepts. The brief adds (4) universe coverage: how much of the top-1000 universe the rule ever links to. **If any of T1–T4 fails, (d) is dropped from v1** (owner decision, #882). If the run is invalid (section 4), the week is repeated, not judged.

The probe does not test whether GDELT's stamps are honest. Under the spec every news row's `known_at` is our fetch time, and the probe records that same stamp.

## 2. What the probe does

`scripts/probe_gdelt.py`, on the spike branch:

| Command | Does | Touches |
|---|---|---|
| `export-universe` | Once, before the week. Writes `universe.csv` (`ticker,name,cik,company_rank`): the universe members at run time with company rank ≤ 1000, with the registrant name known at that time | One short read-only connection to the owner's store. Nothing else ever opens the store |
| `poll` | Fetches `lastupdate.txt` (public HTTPS, no key), then the GKG 2.1 zip it names, unless that file is already held. Then it fetches any earlier 15-minute file it missed (laptop asleep), by its deterministic name, up to 48 hours back and never before the first file held. Each file gets one request, with a 1 s pause between requests and a 5 s, 10 s backoff on errors (3 attempts). A missed file is retried on at most 3 polls, after which it counts as missing. The zip is checked against the listed size and MD5 and stored as fetched under `~/tradepartner-probes/882-gdelt/raw/`. `manifest.jsonl` records `known_at` (our fetch time, UTC), the batch label, the size, the hashes, the `Last-Modified` of `lastupdate.txt`, and whether the file came from `lastupdate` or `catchup` | Network, the probe directory |
| `match` | Re-derives `derived/matches.csv`, `files.csv` and `matched_rows.tsv.gz` from every held zip, offline. It is deterministic and rerunnable | The probe directory |
| `report` | Writes `report.md` and `report.json`, plus `precision_sample.csv` the first time it runs. With `--labels`, it also computes precision and every verdict | The probe directory |

### The matching rule (`RULE_VERSION = name-title-url+ticker-tag/v1`), frozen

Inputs are **only** the `<PAGE_TITLE>` in V2EXTRASXML (HTML-unescaped) and `V2DocumentIdentifier` (the URL). GDELT's organisation, person, theme, tone and actor fields are never read. They are model-extracted (GKG codebook S3; ADR 0008; spec req 2 (d)). A test pins this.

1. **Name key.** Lowercase word tokens of the registrant name. `/XX/` state tags and apostrophes are removed, and legal or structural suffixes are stripped from the end (`inc, incorporated, corp, corporation, co, company, cos, companies, ltd, limited, plc, llc, lp, llp, sa, nv, ag, se, holdings, holding, group, trust, the`), as is a leading "the". A key shorter than 3 characters in total (`3M CO`) gets no name match, only the ticker rule. Share classes of one CIK are one company.
2. **`title_ticker`.** A cashtag (`$AAPL`) or an exchange tag (`NYSE: BRK-B`, `(NASDAQ:AAPL)`; NYSE, NYSE American/Arca/MKT, NASDAQ, AMEX, Cboe, BATS) in the title, matched to a universe ticker. Class separators `- / .` are treated as the same. A bare ticker word never matches.
3. **`title_name`.** The name key appears as consecutive title words. A one-word key must be capitalised in the title, as a proper noun.
4. **`url_name`.** The name key appears as consecutive words of the URL path, case ignored. The host is never matched.

One (row, company) pair is one match. Recall is not measured: a headline saying "Google" does not match "Alphabet Inc.", by design. Coverage (T3) shows what that costs. The probe adds no stop-list for common-word names (Apple, Target, Gap). Precision (T2) measures that cost, and the rule is not tuned during the week.

## 3. Thresholds (pre-registered)

The report, its tests and this table use the same constants (`THRESHOLDS` in the script; a test fails if they drift apart). The window is the **first 7 complete America/New_York days** after the start day. Day boundaries are ET dates of `known_at`, as in spec req 7.

| # | Measure | Threshold | Constant |
|---|---|---|---|
| T1 | Headline share: GKG rows in the window with a non-empty `PAGE_TITLE` | ≥ 90% over the week **and** ≥ 80% on the worst ET day | `headline_share_min` = 0.9, `headline_share_day_min` = 0.8 |
| T2 | Precision of the rule: owner's labels on a seeded simple random sample of 200 distinct (URL, company) matches from the window (seed 882) | ≥ 170 of 200 correct (85%; Wilson 95% lower bound 79.5%) | `precision_correct_min` = 170 |
| T3 | Universe coverage: share of universe companies with ≥ 1 match | ≥ 50% over the week **and** a mean daily share ≥ 15% | `coverage_week_min` = 0.5, `coverage_day_mean_min` = 0.15 |
| T4 | Raw cache in the cheapest form req 5 could keep: full GKG lines of matched rows, gzipped, plus the per-file hashes in the manifest. Bytes per held file × 96 × 365 | ≤ 5 GB per year | `matched_cache_gb_year_max` = 5 |
| info | Whole GKG zips as fetched (req 5 read literally), per day and per year | none: reported. It is expected to fail any plausible budget (section 5) | |
| validity | Files held out of files expected in the window, and 7 complete days | ≥ 90% and 7 days, else repeat the week | `completeness_min` = 0.9 |

### Why these numbers

- **T1 at 90% / 80%.** Spec (d) stores "headline, source, URL". A row without a title is a URL record, not a headline record. The report's lean depends on `PAGE_TITLE` being present on "most rows", and its existence was snippet-only (UNVERIFIED). In two live files (section 5) every row had one. 90% leaves room for crawl failures without accepting a feed in which one row in five has no title. The worst-day floor catches a mid-week format change, which the codebook says should not happen (S3, "stabilized").
- **T2 at 170/200.** Downstream use is per-issuer news counts and event studies (spec, Problem). Misattributed articles add noise in proportion to the false-link share, so 15% contamination weakens an effect by roughly 15%, which is tolerable for a research record. Beyond about 20%, homonyms (a fruit, a store-front verb, a gap-up) would decide what "news about the company" means. With 200 labels, 170 correct puts the 95% Wilson lower bound at 79.5%, so a pass also rules out a true precision below about 80%. Labelling 200 rows from headline and URL takes about an hour. Labelling rule: **1** if the headline or URL refers to the matched company itself, its brand or its products as the company's; **0** for a homonym, a place, a person, another company with a similar name, or anything unsure. Unsure counts as wrong. The report also prints precision per rule (`title_ticker`, `title_name`, `url_name`). That is for information only: there is no per-rule threshold, and the rule is not changed after seeing it.
- **T3 at 50% weekly / 15% mean daily.** The report estimated 3,500–21,000 matched rows a day, from an unverified volume figure and an assumed match share. News is concentrated in mega-caps. If fewer than half of the top 1,000 names get one matched article in a whole week, the collector is a mega-cap feed, and an event study over the universe would have too few news events outside its top few hundred names to be worth a spec. 15% daily (about 150 companies a day) is the floor for a "news day" to be a usable per-name event. Coverage counts raw matches, false positives included, so it is an upper bound. T2 bounds the inflation.
- **T4 at 5 GB per year.** The spec's provisional raw budget is 1 GB for non-EDGAR sources and 5 GB per EDGAR source, to be reset from "one week of forward runs" (req 5). The disk the store lives on was 91% full (18 GB free) on 2026-10-05. A news cache above the EDGAR per-source figure would be the largest single raw consumer on that disk, for a source whose signal use is not yet approved (charter). If only the matched-rows form passes, the plan task for (d) must amend req 5 (a class-B spec change) so that the news raw record is "matched rows plus file hashes". That is the report's follow-up question 2, and this probe supplies its numbers.
- **Validity at 90%.** Missed files lower coverage and bytes, and could hide a format change. Bytes are projected per held file, so gaps do not shrink T4. Coverage, however, is not corrected.

## 4. Owner run instructions

**Before the start (about 5 minutes):**
1. Approve the thresholds above on #882 (one line, "thresholds approved").
2. Check free disk: `df -h ~`. The week needs about 1.5–6 GB under `~/tradepartner-probes/882-gdelt` (section 5). Start only with 10 GB or more free.
3. Make a worktree of the spike and give it its own venv:
   ```bash
   git -C ~/Projects/tradepartner fetch origin
   git -C ~/Projects/tradepartner worktree add ~/Projects/tradepartner-probe-882 origin/spike/882-gdelt-probe
   cd ~/Projects/tradepartner-probe-882 && unset VIRTUAL_ENV && uv sync
   ```
4. Freeze the universe. This is the probe's one read-only connection to your store. Run it after the store repair has finished, and not during an ingest:
   ```bash
   TRADEPARTNER_ENV_FILE=~/Projects/tradepartner/.env STORE__PATH=~/Projects/tradepartner/data/tradepartner.duckdb \
     uv run python scripts/probe_gdelt.py export-universe
   shasum -a 256 ~/tradepartner-probes/882-gdelt/universe.csv   # paste this hash on #882
   ```
   Any CSV with `ticker,name` columns also works (optional `cik`). Put it at `~/tradepartner-probes/882-gdelt/universe.csv` before the first `match`.

**Start** (in a Terminal window you leave open; `caffeinate -i` keeps the Mac from idle-sleeping, and the loop stops by itself after 8 days, i.e. 768 polls, enough for 7 complete ET days):
```bash
cd ~/Projects/tradepartner-probe-882 && unset VIRTUAL_ENV && caffeinate -i zsh -c 'for i in {1..768}; do uv run python scripts/probe_gdelt.py poll; sleep 900; done' 2>&1 | tee -a ~/tradepartner-probes/882-gdelt/poll.log
```
A closed lid still sleeps the Mac. The next poll then fetches the missed files (up to 48 hours back), each stamped with its real, later fetch time.

**Check** (any time, about 1 minute): `tail -5 ~/tradepartner-probes/882-gdelt/poll.log` shows one `ok` line about every 15 minutes. `du -sh ~/tradepartner-probes/882-gdelt/raw` should grow by roughly 0.2–0.8 GB a day. For a mid-week look, run `uv run python scripts/probe_gdelt.py match && uv run python scripts/probe_gdelt.py report`. Then delete `~/tradepartner-probes/882-gdelt/precision_sample.csv`, so that the final sample is drawn from the full week.

**Stop:** the loop ends by itself after about 8 days. To stop it earlier, press Ctrl-C in its window. Then:
```bash
cd ~/Projects/tradepartner-probe-882 && unset VIRTUAL_ENV
uv run python scripts/probe_gdelt.py match
uv run python scripts/probe_gdelt.py report          # writes precision_sample.csv
# label: fill the `correct` column of precision_sample.csv with 1 or 0 (rule in T2), save as labelled.csv
uv run python scripts/probe_gdelt.py report --labels ~/tradepartner-probes/882-gdelt/labelled.csv
```

**Send back on #882:** the contents of `~/tradepartner-probes/882-gdelt/report.md` (the verdict table and the per-day table) and the universe hash. Also attach `labelled.csv`, if you are willing to share 200 headlines and URLs. GDELT's terms allow redistribution with a citation; the publishers' headlines are theirs. Nothing in the probe directory contains secrets.

**Afterwards:** the raw zips are not needed once the report is posted. `rm -rf ~/tradepartner-probes/882-gdelt/raw` frees the space. Keep `derived/` and `manifest.jsonl`.

## 5. Observed at build time (two live files, 2026-10-05 ~03:45Z, Sunday night US time)

| Batch label | Posted (`Last-Modified`) | Fetched (`known_at`) | Zip | Rows | With `PAGE_TITLE` |
|---|---|---|---|---|---|
| 20261005034500 | 03:34:16Z | ~03:45Z | 2.00 MB (6.1 MB CSV) | 467 | 467 |
| 20261005040000 | 03:48:49Z | 03:50:56Z | 2.22 MB | 525 | 525 |

- Format confirmed: 27 tab-separated columns, `PAGE_TITLE` in column 27, the URL in column 5, and HTTP redirecting to HTTPS. Both test files were deleted after the check.
- **The batch label was about 11 minutes *later* than the file's posting time** in both files, so a file was fetchable before the instant on its name. If that holds, GDELT's batch label is a late, not an early, stamp (the report's UNVERIFIED window-label question). The probe records both lags for the whole week (`lag_posted_minus_label`, `lag_fetch_minus_label`). Neither is used as a stamp.
- Night-time volume, about 500 rows per file, implies about 48,000 rows a day if flat, well below the report's unverified 700,000 articles a day. Daytime files are likely larger. The week measures this.
- Whole zips at night-time size: 2 MB × 96 ≈ 0.2 GB a day ≈ **70 GB a year at minimum**. That is why T4 judges the matched-rows form. In a 13-company smoke test the matched rows cost about 5 KB each, gzipped, so T4 passes only if the full universe yields fewer than about 2,700 matched rows a day.
