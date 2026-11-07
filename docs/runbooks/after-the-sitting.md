# Run sheet: the four owner steps after the sitting

Owner and orchestrator only, on the owner's job checkout and real store, after the [data sitting](data-sitting.md)'s releases are closed. The orchestrator runs each step on the owner's go, and the owner decides after each dry run and each stop rule. No team window or subagent runs them. Follow [named data releases](data-releases.md) for the release record and the restore, and [scheduling](scheduling.md) to unload and reload jobs.

**Where this sheet starts (`<OWNER>`, one choice).** Step 3 is a named release, and rule 5 of [named data releases](data-releases.md) keeps a release out of an open paper window. The sitting's **Restart and close** ends with daily's `paper start`. So the owner picks one of two:
- **(a) Before `paper start`.** Run steps 1 to 3 once every sitting release has its `after` row and `health --check` exits 0, with the jobs still stopped and no window open. Then do the sitting's `paper start`. Step 4 may run after the restart.
- **(b) After the restart.** Run steps 1, 2 and 4 with daily's window open. Step 3 waits until no window is open. Step 4 then seeds without it or waits too (**Why this order**, last paragraph).

The code for all four is merged. What is left are runs that only the owner can make:

| Step | Task | What it writes | Release | Rough time | Rough disk |
|---|---|---|---|---|---|
| 1 | T246 (#1598) | `BENCH:RSP` bars and actions (check-only); the check writes nothing | none | minutes for the backfill (about 130 months); the check itself not measured | under 1 GB |
| 2 | T248f (#1762) | `mergers`, `symbol_changes`, spin-off `corporate_actions` rows, 2020-06 on | none | not measured (about 78 monthly scans, run twice) | one store copy |
| 3 | T164e (#1358) | `filing_events`, every issuer's 8-K and 8-K/A | **named** | three full EDGAR passes, about 1.5 to 2 h each | one store copy, plus a new table, size not measured |
| 4 | T324 (#2629) | `filing_index` and its three state tables, then one full build | none | about 8 min for the index plus one full build (about 1.5 to 2 h) | one copy, about 2.2 GB of table, two comparison copies |

`<OWNER>` marks a value the code and docs do not settle. Fill it in before the step; never guess it.

## Why this order

1. **T246 first.** It is the only known-answer check of the store after the sitting rewrote history across its releases. Run it on the sitting's store alone, before anything else writes. A defect it finds stops the rest of this sheet until the owner has read it. Its only write is RSP, which is check-only: `data_vintage` leaves RSP out, so no trial goes stale ([benchmark backfill](benchmark-backfill.md#rsp-a-check-only-series-1598-t243)).
2. **T248f second.** It is Alpaca-only and touches no EDGAR table. The rows it writes (`mergers`, `symbol_changes`, `spinoff` actions) move no default trial vintage (`store/registry.py`, `_TRIAL_FACT_TABLES` and the `data_vintage` docstring). So its plan check, "`lab status` with no stale trial", can be read cleanly only before step 3, whose release stales trials. Two kinds of trial are the exception: a trial frozen with `adjust.apply_spin_offs = true` counts `spinoff` rows, and one frozen with `backtest.delisting_failure_return` other than `none` counts `mergers` (`data_vintage`'s `spin_offs` and `delisting_policy` gates). Those may go stale.
3. **T164e before T324.** `edgar.filing_events_enabled` and `edgar.event_forms` are hashed into the build fingerprint (`store/build_fingerprint.py`, `HASHED_SETTINGS_KEYS`). The seed records that fingerprint. If the switch is turned on after the seed, every incremental night fails its EDGAR source and names `--rebuild-index`, which is another full build. So the switch must have its final value before the seed.
4. **T324 last.** Its seed records the fingerprint of the settings and code in effect. Its five nights of evidence start only after the key is on.

If step 3 cannot run (see its first stop rule), the owner chooses: run T324 without it, and accept one more `--rebuild-index` when the switch is turned on later, or hold T324 until step 3 has run. `<OWNER>`.

## Before any step

- [ ] Every release the sitting opened has an `after` row, and `uv run tradepartner health --check` exits 0. Record the entry point chosen above, and which windows are open (`uv run tradepartner paper status --all`).
- [ ] Nothing else is writing: `pgrep -f "tradepartner "` is empty, and no sweep is running.
- [ ] **The two jobs.** The paper run is at 08:05 ET and the ingest at 18:30 local. A scheduled writer waits `store.writer_lock_retry_seconds` (300 s) for the store lock, then fails. Start a long step only when it will finish before the next job. Otherwise unload that job first and reload it after ([scheduling](scheduling.md)). With a paper window open, an unloaded paper job means a missed run, so prefer the gap between 08:05 and 18:30.
- [ ] **Disk.** Before every `store backup`, check free space and stop if it is under what the step needs, plus `ingest.min_free_disk_gb` (5 GB; ingest refuses below it). `health.warn_free_disk_gb` is 15.

  ```bash
  df -h data
  ls -lh data/tradepartner.duckdb data/tradepartner.duckdb.wal
  ```

- [ ] Keep every printed backup path, with its `ls -lh` size and `shasum -a 256` hash, on the step's issue. Delete no copy without the owner's word.

## 1. T246: RSP backfill and the index check (#1598)

Purpose: store RSP as the check-only equal-weight series, then run `index-check`. It compares the whole universe, equal and cap weight, with RSP and SPY by year and month, 2019-08 to 2026-09. It is never a trial: no store write, no N. Plan line: backtest T246.

**Preconditions.** No release is open. No ingest is running: `backfill-benchmark` waits `store.lock_retry_seconds` for a writer, then fails with `store busy`. The Alpaca keys are in `.env`.

- [ ] **Backup.** Take one before the first write of this sheet: `uv run tradepartner store backup`. Keep its printed paths as `<post-sitting-copy>`. The plan line asks for none, but it is the restore point if the RSP backfill goes wrong, and it is the store the check read.
- [ ] **Seed values.** `--cik`, `--name` and `--exchange` are labels that no read uses. They are needed only when no benchmark security lists RSP yet. The name is the issuer's display name. The other two were never checked against the issuer ([benchmark backfill](benchmark-backfill.md#rsp-a-check-only-series-1598-t243)):
  - `--cik`: the CIK of the trust that files for the fund, from EDGAR. `<OWNER>`.
  - `--exchange`: the NYSE Arca code as Alpaca spells it. `<OWNER>`.
- [ ] **Backfill.** It has no dry run. A rerun adds nothing it already has, so it is safe to repeat.

  ```bash
  uv run tradepartner backfill-benchmark RSP --since 2016-01-01 \
    --cik <OWNER: trust CIK> --name "Invesco S&P 500 Equal Weight ETF" \
    --exchange <OWNER: Alpaca's NYSE Arca code>
  ```

  Expected: `RSP: BENCH:RSP seeded` (or `already in the store`), then one `alpaca: <status>, <rows> rows, …` line per month, and exit 0 when every month is `ok`. It writes no `ingestion_runs` row, so the printed lines are the only record. Keep them.
- [ ] **The check.** It opens no store handle for writing.

  ```bash
  uv run tradepartner index-check --out <OWNER: research library>/index-check-<YYYY-MM-DD>.json
  ```

  Expected: the header (`index-check 2019-08 to 2026-09 (not a trial, nothing written)`, store, schema version, code version, data vintage, universe, `adjust.apply_spin_offs`, `edge E`), then the by-year table, the zone totals, the by-month table and the worst months with their names. Months after the protected edge print `·`, never a level.

**Stop rules.**
- The backfill exits 2 because another security already lists `RSP`, or `BENCH:RSP` is taken. Stop and report on #1598 (spec open question 36). Do not read that security by ticker.
- A month is `stale` or `failed`. Nothing is written for that month, and earlier months stay. Read the message (it names the sessions), then rerun the same command. If Alpaca has no bar for a session, record that on #1598 and ask before narrowing `--since`.
- `index-check` exits 2 (refused) or 1 (failed), or a protected month shows a level. Stop and paste the output on #1598.

**After.** Paste the printed output on #1598. `quant-auditor` reads it against the first pass of 2026-10-12. Each new defect becomes its own issue, as #1613 to #1615 did. A defect in the sitting's result is read by the owner before step 2.

## 2. T248f: the merger backfill from 2020-06-01 (#1762)

Purpose: ask Alpaca for cash, stock and stock-and-cash mergers by process-date month from 2020-06-01, so T328c's merger exit math and T329's coverage counts have rows to read. Without it, every acquiree exit before the daily merger ingest began falls back to the last close. The same scan also asks spin-offs and name changes. Plan line: data-foundation T248f. Its deps, T248d (#2072) and T248e (#2075), are merged.

**Preconditions.** The plan's gate: no ingest, sweep or `paper run` while it runs. `--since` may not be before `alpaca.actions_backfill_floor` (2020-06-01); the CLI refuses earlier. The Alpaca keys are in `.env`.

- [ ] **Trials that may stale.** List the registered trials frozen with `adjust.apply_spin_offs = true` or with `backtest.delisting_failure_return` other than `none` (read-only, from the trial registry). Only those may go stale from this step. `<OWNER>`: whether a stale among them is a stop, since the plan and spec say "no stale trial".
- [ ] **Books.** The `mergers` table's one reader is paper reconciliation (`_TRIAL_FACT_TABLES` note), and it explains an acquirer position at the stored merger ratio. Save the BOOK IDS list as in sitting step 9: every `security_id` with an open lot or a latest `positions_daily` row, per book.
- [ ] **Dry run, then the real run.** Each month is its own chunk. A dry run fetches and reports each month, then rolls back its writes and its run row.

  ```bash
  uv run tradepartner ingest --source alpaca --actions-backfill --since 2020-06-01 --dry-run
  uv run tradepartner store backup
  uv run tradepartner ingest --source alpaca --actions-backfill --since 2020-06-01
  uv run tradepartner health --check
  uv run tradepartner health
  uv run tradepartner lab status
  ```

  Expected: one line per month, `alpaca: filled, <rows> rows, cursor actions;since=2020-06-01;through=<day>: <first>..<last>: <n> spin-off action(s), <n> name change(s), <n> merger(s); …` and then the counts. In a dry run the message starts with `dry run:`. The merger counts are `mergers_stored`, `merger_rows_refused`, `actions_unresolved`, `merger_self`, `merger_unordered`, `merger_conflict` and `actions_not_yet_known`. The run exits 0 when every month is `filled`. `through` defaults to today, New York time. `health` shows the mergers block, and `lab status` shows no stale trial.

**Stop rules.**
- A month that is not `filled`. The run stops at that month, and earlier months stay. A rerun asks again from `--since`: a repeat adds nothing, and a changed value is a revision stamped at the run.
- `health --check` fails, including the four merger rules (`mergers_known_at_from_process_date`, `mergers_rates_match_kind`, `mergers_acquirer`, `mergers_unique`). Restore from the backup as in [named data releases](data-releases.md), after saving the failed state.
- `lab status` shows a stale trial that is not on the list above. Stop and report on #1762.
- A row written on a BOOK ID. Check it read-only right after the write and before the next `paper run`:

  ```sql
  SELECT security_id, acquirer_security_id, kind, effective_date, process_date, known_at
  FROM mergers
  WHERE ingested_at >= <this run's start>
    AND (security_id IN (<BOOK IDS>) OR acquirer_security_id IN (<BOOK IDS>));
  ```

  Any row stops the step. Report it on #1762 before the paper job runs again. A merger processed after its effective date can halt reconciliation (data-foundation spec, open question 41, owner decision (a): accept a one-time halt).
- `merger_conflict` (two ids of one deal) and `merger_unordered` are report-only. Read them and paste them; they are not stops.

**After.** Post on #1762: the counts per month by kind (stored, refused, unresolved, not yet known). Add one cash merger and one stock merger checked by hand against the deal's public terms and the acquiree's last close. Add `health --check` exit 0, `lab status`, and, after the next 18:30 ingest, that daily run's merger figures from `health`'s mergers block. `quant-auditor` reads the evidence.

## 3. T164e: the first `filing_events` fill and the default flip (#1358)

Purpose: turn `edgar.filing_events_enabled` on for the owner's store, fill `filing_events` (0 rows today), and pass the spec's four conditions so the default can be flipped. It is item 1 of the 8-K Item 2.02 earnings build list (#2892, [the design](../research/2026-11-02-earnings-event-family-design.md)). Plan line: data-foundation T164e. Spec: data-foundation, amendment 2026-10-09, "Ingest, config and health (T164e)".

**Why a named release.** `filing_events` is in `_TRIAL_FACT_TABLES`, so its rows enter every trial's `data_vintage`. The fill stales every registered trial with a cutoff after the first stored 8-K. The next plain `sweep run` reruns them, and each rerun counts in N. The spec puts the run under a named data release (ADR 0016 point 7).

**Preconditions.**
- [ ] **No open paper window.** Run `uv run tradepartner paper status --all`. [Named data releases](data-releases.md) rule 5 forbids a release that touches a session inside an open paper window. This fill writes a row for every 8-K accepted since that window opened. If a window is open, stop: this step waits (entry point (b) above).
- [ ] The sitting's step 9 (`rederive-1682`, T198) is closed. The plan line's gate waits on it.
- [ ] #1382 is closed with the owner's decision. It is closed; confirm that its closing comment is the decision (the spec's fourth condition). The design also asks to confirm that `restamp-1382a3` is done or does not touch universe 8-K stamps. Sitting step 3 records it as done.
- [ ] `<edgar.cache_dir>/bulk/submissions.zip` and `companyfacts.zip` exist (`ls -lh`). Under `--bulk-from-cache` the items refresh reads the cached `submissions.zip` and its older pages with no request. Without the flag it asks the per-CIK API once per issuer CIK still in the old stamps layout (the request count is not measured). The flag also takes company facts from the cached `companyfacts.zip`, so a fact newer than that file arrives on the next nightly. The commands below use the flag, which is the design's cheap path. `<OWNER>` may drop it.
- [ ] **The release name.** `<OWNER>`. A name is used once.

**The switch for these runs.** Settings read nested environment variables, and the process environment beats `.env`. So each command below sets `EDGAR__FILING_EVENTS_ENABLED=true` for itself alone. The config key is `edgar.filing_events_enabled`.

- [ ] **Dry run first, before the release.** It writes no store row, so `decision data-release open`'s check still passes afterwards. The adapter has no dry-run mode (`adapters/edgar_source.py`), so the dry run's fetch pass already does the one-time items refresh into the stamps cache. That refresh writes `items` and the refreshed flag only, never a stamp (spec, "One refresh rule").

  ```bash
  EDGAR__FILING_EVENTS_ENABLED=true uv run tradepartner ingest --source edgar --bulk-from-cache --dry-run
  ```

  Read the `filing_events_*` counts in the EDGAR line: `filing_events_added`, `filing_events_unstamped`, `filing_events_items_missing`, `filing_events_not_yet_known` and `filing_events_clock_suspect`. The rest of the line should match an ordinary nightly. A difference outside `filing_events` is a stop.
- [ ] **The real run, under the release.**

  ```bash
  uv run tradepartner store backup
  uv run tradepartner decision data-release open --name <OWNER: release name> --backup <events-copy> --reason "#1358 T164e first filing_events fill"
  EDGAR__FILING_EVENTS_ENABLED=true uv run tradepartner ingest --source edgar --bulk-from-cache
  EDGAR__FILING_EVENTS_ENABLED=true uv run tradepartner health --check
  EDGAR__FILING_EVENTS_ENABLED=true uv run tradepartner health
  ```

- [ ] **The second run must add zero rows** (the spec's second condition). Run it once the first has finished, before closing the release:

  ```bash
  EDGAR__FILING_EVENTS_ENABLED=true uv run tradepartner ingest --source edgar --bulk-from-cache
  ```

  `filing_events_added` should be 0. A row accepted after the first run's clock is legitimately new. So when the count is not 0, check that this is 0 (read-only):

  ```sql
  SELECT count(*) FROM filing_events
  WHERE ingested_at = <second run's ingested_at> AND accepted_at <= <first run's started_at>;
  ```

- [ ] **Close the release.** `--from` and `--to` are the XNYS sessions of the earliest and latest stored `filing_events.known_at`. Read both by query after the write; stop if a session cannot be placed.

  ```bash
  uv run tradepartner decision data-release close --name <OWNER: release name> --from <first touched session> --to <last touched session> --reason "#1358 filing_events rows by form; trials staled"
  uv run tradepartner lab status
  ```

- [ ] **Coverage** (the third condition). For each calendar quarter 2020Q3 to 2023Q4, take the share of the universe at the quarter's last session close (`universe_as_of`) whose CIK has an `8-K` with Item `2.02` **accepted within that quarter**, as TP-1 defined it on PR #1381. It is per quarter, never cumulative: `filing_events_as_of(..., forms=["8-K"], items=["2.02"])` at a quarter end returns all earlier history too, so the query must filter on `accepted_at` inside the quarter. Each quarter must be within one percentage point of TP-1's column; the evidence tables are on PR #1381. No repo command computes this. Prepare and review the read-only query first. `<OWNER>`.

**Stop rules.**
- `health --check` fails, either on a `filing_events` rule (`known_at ≠ accepted_at`, a form outside `edgar.event_forms`, NULL `items`, a duplicate `(cik, accession)`) or on any other rule. Follow [named data releases](data-releases.md) to restore `<events-copy>`, after saving the failed state, and do not run `close`.
- The second run adds a row accepted at or before the first run's start.
- `filing_events_clock_suspect` or `filing_events_items_missing` above 0. `health` warns on either (spec). `<OWNER>`: whether that blocks the flip. A clock-suspect count is a stamps-clock signal (#1382).
- A quarter more than one point off TP-1. Do not flip the default (spec).

**Step 3 is done** when the owner has decided the flip below: either it passes and the switch is on for every later run, or the owner writes down that it stays off. Step 4 reads that value.

**The flip** (the owner's, a config change). Only when all four conditions hold: `health --check` green, a zero-row second run, coverage within one point, and #1382 closed.
- The default: `edgar.filing_events_enabled = true`, a small PR changing `filing_events_enabled: bool = False` to `True` in `src/tradepartner/config_edgar.py`. The design lists it as class B (config).
- Until that PR merges, add `EDGAR__FILING_EVENTS_ENABLED=true` to `.env` so the 18:30 ingest keeps asking. With the switch off, the nightly asks nothing, and the table goes stale. The nightly's extra time with the switch on, on the full walk, is not measured.
- On the incremental nightly (step 4), `filing_events` is built per touched CIK. `_write_indexed_filings` calls `_ingest_filings`, which writes `filing_events` while the switch is on. That answers the design's question on whether the nightly still asks it. Step 4's comparison includes the table.

**After.** Post on #1358 (or the flip PR): the run-row counts, the second run's count, the quarterly coverage against TP-1, `health --check`, and the release ids.

## 4. T324: incremental EDGAR, the seed and the key (#2629)

Purpose: seed `filing_index` with one full walk, prove one incremental night equals a rebuild, then turn `edgar.incremental_index` on. This is the step that ends the ~2 h, ~4 GB nightly EDGAR pass (the owner's 2026-10-30 "design defect"). Plan line: data-foundation T324. Spec: data-foundation, amendment 2026-10-30 (#2629), "Full rebuild and seed". All its code (T317 to T323b) is merged.

**What the seed does.** `ingest --source edgar --rebuild-index` walks every quarter from `edgar.index_first_year` (1993). It commits one quarter per chunk as a `partial` `edgar_index` run row, and checks the disk floor before each chunk. Then it runs one full EDGAR build and writes the `ok` `edgar_index` row with the build fingerprint. A stopped seed resumes after its last committed quarter, but only under the same fingerprint, so change no setting or code between a stop and the rerun. The four index tables are out of `data_vintage`, so no trial goes stale. The spec calls the seed an owner step with a backup, not a release.

**Size and time.** T317's probe seeded a temp copy with 15.3M rows, 2.18 GB, in 487 s (`changelog.d/2659-t317-edgar-probe.md`). The final build is a full pass; the nightly's full pass took 97 to 115 min on 2026-10-26 to 10-28 (#2593). Peak memory of the full pass was about 4.1 GB after #2622.

**Preconditions.**
- [ ] Step 3 is done (its flip decided), or the owner has chosen to seed without it (see **Why this order**). Every hashed setting must have its final value now, `edgar.filing_events_enabled` included. If the switch is on, it must be on for the seed itself: `EDGAR__FILING_EVENTS_ENABLED=true` in `.env`, or the default-flip PR merged, not a per-command override. Confirm the effective value with `uv run tradepartner health`, which reports the switch's state. A seed run with the switch off hashes `false`, and turning it on later closes the gate.
- [ ] **Code.** The sitting's pull (step 5) already brought every T324 dependency. Pull nothing between the seed and turning the key on. A later pull that changes the schema, a cache version or `BUILD_RULES_VERSION` (7 today) changes the fingerprint and closes the gate again. If the owner pulls before the seed, record both hashes and the commit count, as in sitting step 5.
- [ ] **Disk.** The live store grows by about 2.2 GB. The comparison needs two copies of the seeded store. Required free space is about `S + 2 × (S + 2.2) + 5` GB, where `S` is the store plus WAL size now. Stop if `df -h data` shows less.
- [ ] Outside the 08:05 and 18:30 jobs. The seed takes most of a working day's gap, so unload the 18:30 ingest if it might overlap ([scheduling](scheduling.md)).

- [ ] **The seed.** No dry run first. On an unseeded store, `--rebuild-index --dry-run` rolls back every quarter chunk, so its final build reads an empty `filing_index` and shows nothing the seed will write. The plan and spec ask for none here (the spec's monthly dry run is on a seeded copy).

  ```bash
  uv run tradepartner store backup
  uv run tradepartner ingest --source edgar --rebuild-index
  uv run tradepartner health --check
  ```

  Expected: one final `edgar: ok, <rows> rows, cursor rebuild_index: …` line and exit 0. The final build's derived rows should be what an ordinary nightly would add. A seed stopped on disk prints `seed stopped: … below ingest.min_free_disk_gb …` and writes no row for that chunk; free space and rerun the same command. Afterwards, read the chunk rows (read-only):

  ```sql
  SELECT chunk_cursor, status, started_at, finished_at, rows_added
  FROM ingestion_runs WHERE source = 'edgar_index' ORDER BY started_at;
  ```

  Every quarter has a `partial` row, and the last row is `ok`, with `index seed complete` in its message.
- [ ] **The comparison, on two copies** (the plan's and spec's "one comparison on a copy of incremental against `--rebuild-index`, which must show no difference"). Right after the seed, take two copies: two `store backup` calls, a minute apart. They are scratch stores, not backups: label them so on #2629, never restore from them, and delete them after the comparison on the owner's word. The pre-seed backup is the restore point; never run a command on it. Each command points the CLI at a copy with `STORE__PATH`. Both runs still use the shared `edgar.cache_dir` and make SEC requests. Run them one after the other, never together and never during the 18:30 ingest: each process has its own `edgar.requests_per_second` limiter (9), and SEC's ceiling is 10.

  Run them after SEC has listed at least one daily index file since the seed. Otherwise the incremental run has nothing new to read. Run them back to back when SEC accepts no filings, a Saturday for example: copy B's full build starts one to two hours after copy A's run, and a filing or companyfacts update in between would reach only B and show as a false difference. If they cannot run in such a window, the comparison query must list the rows known between the two runs' starts separately.

  ```bash
  uv run tradepartner store backup   # <copy-A>
  uv run tradepartner store backup   # <copy-B>, the next minute
  EDGAR__INCREMENTAL_INDEX=true STORE__PATH=<copy-A> uv run tradepartner ingest --source edgar
  STORE__PATH=<copy-B> uv run tradepartner ingest --source edgar --rebuild-index
  EDGAR__INCREMENTAL_INDEX=true STORE__PATH=<copy-A> uv run tradepartner health
  STORE__PATH=<copy-B> uv run tradepartner health
  ```

  The spec's test: the two stores are equal table by table and row for row, `filing_events` included. Their run-row messages (chain candidates, unmatched names, missing benchmarks, unstamped counts) and their `health` output are equal too. No repo command does this comparison. Prepare and review a read-only query first. It must list which columns it leaves out: those the run's own clock sets differ by construction (`ingested_at`, and the `known_at` of a `filed_on` revision row, #2703). `<OWNER>`.

**Stop rules.**
- The seed exits 1 for any reason other than the disk floor.
- `health --check` fails after the seed: restore the pre-seed backup as in [named data releases](data-releases.md), after saving the failed state.
- Any difference between copy A and copy B, outside the listed clock columns: do not turn the key on. Paste the difference on #2629.
- The incremental run on copy A fails its source and names `--rebuild-index`: the fingerprint changed after the seed. Stop and ask.

**Turning the key on** (the owner's, a config change). The key is `edgar.incremental_index = true`. Set it in the jobs' environment, `EDGAR__INCREMENTAL_INDEX=true` in `.env`, before the first 18:30 ingest **after the comparison passes**. The plan says "the first night after the seed", but the comparison needs a new daily file and a second full pass, so it cannot pass before that night. The nights between the seed and the key are ordinary full walks with the key off. A settle in that gap is caught by T319b's rule. The default stays `false`: it is the rollback, the full walk. The plan line names no default-flip PR.

**After.**
- [ ] Post on #2629 the wall time and peak RSS of **5 consecutive scheduled runs**, from their `edgar_index` run rows (`started_at`, `finished_at`, and `peak_rss_bytes` in `message`):

  ```sql
  SELECT started_at, finished_at, status, message FROM ingestion_runs
  WHERE source = 'edgar_index' AND mode = 'session' ORDER BY started_at DESC LIMIT 5;
  ```

  The pass mark: at most 20 min on an ordinary weekday, at most 1.5 GB, and at most 60 min on the first settle night. Read T317's listing-time log alongside.
- [ ] Post the count of 13D rows refused as `changed` against their first-known `filed_on` (#2703, the spec's bounded case). The EDGAR run row's `sec_forms_changed` counts every ownership form, so it is an upper bound. `<OWNER>`: the per-form split.
- [ ] Schedule the monthly `--rebuild-index --dry-run` on a store copy, outside the jobs (spec, "A rule changed in code without a bump"). How it is scheduled is `<OWNER>`.

## Open questions for the owner

- **The entry point.** (a) steps 1 to 3 before daily's `paper start`, or (b) after it, with step 3 waiting for a closed window. Rule 5 keeps T164e's named release out of an open window.
- **T164e before or after T324.** The switch is in the fingerprint, so turning it on after the seed costs one more full `--rebuild-index`.
- **T164e's cost.** Its dry run, real run and zero-row rerun are three full EDGAR passes on the full walk.
- **Values no doc settles.** RSP's CIK and Alpaca exchange code; the `index-check --out` folder; T164e's release name and coverage query; T324's comparison query and the monthly dry-run schedule.
