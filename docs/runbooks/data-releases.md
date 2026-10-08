# Runbook: named data releases

Owner-run, on the real store. Agents never change the owner's store. Plan task T140 ([#1325](https://github.com/josejuarez96/tradepartner/issues/1325), plan amendment [#1319](https://github.com/josejuarez96/tradepartner/issues/1319)). The owner accepted the policy on [#1303](https://github.com/josejuarez96/tradepartner/issues/1303).

Until T140b adds a store kind for it, the record is a hand-written TOML file. Start recording now. Nothing here waits for T140b.

## Why

A repair that changes stored rows changes what a backtest reads. The trials already run on those sessions then no longer describe the store. Two things follow:

- **Sweeps go stale.** A trial is current only while the data vintage at its cutoff is unchanged (strategy-lab spec, Definitions, "Vintage"). A repair that writes a row known on or before a trial's cutoff makes that trial stale. The next plain `sweep run` reruns every stale variant, and **every rerun counts in N** (it raises the bar every later promotion must clear). So keep releases few and large: one batch, not one name a night.
- **Old results must stay traceable.** A trial is only checkable against the store it read. The backup taken before each release is that store. The record says which backup holds which state.

## The rule

1. **Every row-changing repair on the owner's store is one named release.** This covers `repair-resolution` (a real run), `master-retract`, `repair-bars` (T140c) and any hand fix that inserts, updates or deletes rows. The nightly `ingest` is not a release.
2. **Before it:** take a file backup named `tradepartner.<release>.duckdb`, beside the store, then write a `before` record.
3. **After it:** write an `after` record, with the sessions the repair touched.
4. **One batch per release.** Do every fix the release is for, in one sitting, then close it. Do not open a second release while one is open.
5. **Paper windows are off limits.** Once T71 starts, no release touches a session inside an open paper window. The daily ingest is the only writer there (#1303 accepted policy; [ADR 0016](../decisions/0016-development-boundary-and-forward-exams.md), point 7). A forward exam compares the paper book with a backtest recomputed from the store, so a repair there would move the exam with no change in the book.
6. **Run it outside ingest windows and outside a running sweep.** A sweep running through a release fails its open variants with `store changed during run`.
7. **Keep every backup listed.** Each `before` and `record` entry names its backup file. If a data licence ever needs its data deleted (the deletion clause T145 will set), the list of backups is the list of files to walk. Do not delete a backup without noting it in the record (a `reason` on a new `record` entry is enough).

## The record: `data/releases.toml`

The file lives beside the store, in `data/`. `data/*` is gitignored, so it never reaches the public repo. Write it by hand. Append; never edit an earlier entry. T140b imports it once into the store; after that, its commands write the record.

Each entry is one `[[release]]` table. An example (the numbers are illustrative):

```toml
[[release]]
name = "repair-13-tickers"
stage = "before"
made_at = 2026-10-10T14:05:00Z
backup_path = "data/tradepartner.repair-13-tickers.duckdb"
store_max_ingested_at = 2026-10-09T05:48:12.523886Z
data_vintage = 2026-10-09T05:48:12.523886Z
cutoff = 2026-04-29T20:00:00Z
sessions_from = 2016-01-04
sessions_to = 2026-04-29
reason = "#1314: 13 names hold another company's prices; repair after the T114 sweep"
```

The fields:

| Field | On | Meaning |
|---|---|---|
| `name` | all | The release name. Lowercase, digits and hyphens. The backup file carries it: `tradepartner.<name>.duckdb`. A `before` and its `after` share one name. |
| `stage` | all | `before` (written before the repair), `after` (written after it) or `record` (a closed note about a state that already exists, such as the back-fill below). |
| `made_at` | all | When you wrote the entry. UTC, with the `Z`. |
| `backup_path` | `before`, `record` | The backup file, as a path from the project root. Leave it out on `after` (the `before` names it). |
| `store_max_ingested_at` | all | The store's latest `ingested_at` at that moment (the command below prints it). |
| `data_vintage` | all | The data vintage at `cutoff`: the latest `ingested_at` over fact rows with `known_at` on or before `cutoff`. |
| `cutoff` | all | The UTC time the vintage is computed at. For `before` and `after`: the close of `sessions_to`. For `record`: the trial's `data_cutoff`. |
| `sessions_from`, `sessions_to` | all | On `before`: the sessions you plan to touch. On `after`: the sessions the repair actually touched. On `record`: the trial's window. |
| `trial` | `record` | The trial id whose state the backup holds. |
| `reason` | all | One line: why, and the issue number. On `after`, say anything that differs from the plan. |

A delete-only repair (bars removed, nothing written) leaves `data_vintage` unchanged, because the vintage reads only rows still there. Until T140b counts `after` records in the vintage, such a release does not make trials stale on its own. Note it in `reason`, and rerun any sweep over those sessions with `--rerun` if its results matter.

## Read the two numbers

This opens the store read-only, so it is safe whenever no writer holds the store. Pass the store path and the cutoff:

```bash
cd ~/Projects/tradepartner
uv run python -c "
import sys, duckdb
from datetime import datetime
from tradepartner.store.registry import data_vintage, store_max_ingested_at
conn = duckdb.connect(sys.argv[1], read_only=True)
conn.execute(\"SET TimeZone='UTC'\")
cutoff = datetime.fromisoformat(sys.argv[2])
print('store_max_ingested_at =', store_max_ingested_at(conn).isoformat())
print('data_vintage =', data_vintage(conn, cutoff).isoformat())
" data/tradepartner.duckdb 2026-04-29T20:00:00+00:00
```

Write the printed times into the entry with a `Z` in place of `+00:00`.

## Step by step: one release

1. **Pick a name and the sessions.** The name says what the batch is (`repair-13-tickers`). List the securities and sessions the repair will touch.
2. **Check the gates.** No paper window covers those sessions (once T71 starts). No ingest is running and none is due. No sweep is running. Stop the scheduled jobs as in [scheduling.md](scheduling.md), "Before pulling a schema migration".
3. **Back up the store.** With nothing running:
   ```bash
   cp data/tradepartner.duckdb data/tradepartner.<name>.duckdb
   [ -f data/tradepartner.duckdb.wal ] && cp data/tradepartner.duckdb.wal data/tradepartner.<name>.duckdb.wal
   ```
   Check free disk first (`df -h`): the store is over 1 GB.
4. **Write the `before` entry.** Run the command above on the live store, at the close of `sessions_to`, and append the entry.
5. **Run the repair.** Every command in the batch, back to back. Dry runs first where the command has one.
6. **Check it.** `uv run tradepartner health --check` exits 0, and the checks the release's issue names pass.
7. **Write the `after` entry.** Same `name`, `stage = "after"`, the numbers read again on the live store, and the sessions actually touched.
8. **Restart the jobs** (scheduling.md). The next plain `sweep run` reruns any stale variants; each rerun counts in N.

If the repair fails partway: restore the backup (scheduling.md, "To restore from a copy"), then write an `after` entry whose `reason` says it was rolled back. The release is closed; a retry is a new release with a new name.

## The back-fill: the state H1's holdout trial read

H1's holdout trial (trial 4, `h1-momentum-12-1`, kind `holdout`) ran on 2026-10-08, before any release was recorded. The record gets one closed `record` entry for it, so the store state it read is named before the first repair (T141) changes the store.

The backup is `data/tradepartner.pre-sweep-20261008.duckdb`, the newest backup taken before T141. It was checked read-only:

- Trial 4's row in that backup: window 2023-12-29 to 2026-09-30, `data_cutoff` 2026-09-30T20:00:00Z, `data_vintage` 2026-10-08T02:48:17.200099Z, `store_max_ingested_at` 2026-10-08T05:48:12.523886Z, started 06:01:22Z, finished `ok` at 06:46:34Z.
- `data_vintage` computed on that backup at the same cutoff: 2026-10-08T02:48:17.200099Z. **Equal.** The backup's `store_max_ingested_at` is also equal to the one on trial 4's row, so no ingest ran between the trial and the backup. The backup is the state trial 4 read.
- The two older backups (`pre-ingest-20261008`, `pre-reingest-20261007`) predate trial 4 and do not contain it.

The entry (its `made_at` is when it is written):

```toml
[[release]]
name = "pre-sweep-20261008"
stage = "record"
made_at = 2026-10-08T21:50:00Z
backup_path = "data/tradepartner.pre-sweep-20261008.duckdb"
store_max_ingested_at = 2026-10-08T05:48:12.523886Z
data_vintage = 2026-10-08T02:48:17.200099Z
cutoff = 2026-09-30T20:00:00Z
sessions_from = 2023-12-29
sessions_to = 2026-09-30
trial = 4
reason = "Back-fill (#1303 item 1): the state H1's holdout trial 4 read. data_vintage at trial 4's cutoff on this backup equals trial 4's stored data_vintage, and store_max_ingested_at equals trial 4's, so the backup is that state; no ingest between."
```

Keep this backup until H1's holdout result no longer needs checking. It is the only copy of the state the holdout was spent on.
