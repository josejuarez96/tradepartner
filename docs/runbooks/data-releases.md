# Runbook: named data releases

For the post-T162 sequence of releases and paper-window handoff, use the [one data sitting run sheet](data-sitting.md).

Owner-run, on the real store. Agents never change the owner's store. Plan task T140 ([#1325](https://github.com/josejuarez96/tradepartner/issues/1325), plan amendment [#1319](https://github.com/josejuarez96/tradepartner/issues/1319)). The owner accepted the policy on [#1303](https://github.com/josejuarez96/tradepartner/issues/1303).

The record of each release lives in the store, as `data_release` rows of the `owner_decisions` table. You write it with `uv run tradepartner decision data-release open | close | record` (plan task T140b, built; `store/registry.py`, `cli.py`). The hand-written `data/releases.toml` of the early releases is the legacy format: import it once with `decision data-release import-file` (see "The record").

## Why

A repair that changes stored rows changes what a backtest reads. The trials already run on those sessions then no longer describe the store. Two things follow:

- **Sweeps go stale.** A trial is current only while the data vintage at its cutoff is unchanged (strategy-lab spec, Definitions, "Vintage"). A repair that writes a row known on or before a trial's cutoff makes that trial stale. The next plain `sweep run` reruns every stale variant, and **every rerun counts in N** (it raises the bar every later promotion must clear). So keep releases few and large: one batch, not one name a night.
- **Old results must stay traceable.** A trial is only checkable against the store it read. The backup taken before each release is that store. The record says which backup holds which state.

## The rule

1. **Every row-changing repair on the owner's store is one named release.** This covers `repair-resolution` (a real run), `master-retract`, `repair-bars` (T140c) and any hand fix that inserts, updates or deletes rows. The nightly `ingest` is not a release.
2. **Before it:** run `uv run tradepartner store backup`, then `decision data-release open` with its printed copy path.
3. **After it:** `decision data-release close`, with the sessions the repair touched.
4. **One batch per release.** Do every fix the release is for, in one sitting, then close it. Do not open a second release while one is open.
5. **Paper windows are off limits.** Once T71 starts, no release touches a session inside an open paper window. The daily ingest is the only writer there (#1303 accepted policy; [ADR 0016](../decisions/0016-development-boundary-and-forward-exams.md), point 7). A forward exam compares the paper book with a backtest recomputed from the store, so a repair there would move the exam with no change in the book.
   **One exception (2026-10-11, #1497, owner-approved; [ADR 0016](../decisions/0016-development-boundary-and-forward-exams.md) point 7 carries the note).** A filing stamp inside the window may be corrected **earlier**, to the time its SGML header gives, by at most New York's offset (a stamp the SEC's API served late), when a read-only pre-check shows that no paper decision read the row. The pre-check is `scripts/restamp_precheck.py` (data-foundation plan, amendment 2026-10-11, decision (d), which defines it): the open windows, every instant a paper run of any kind read the store at (each window's `started_at`, `close(S-1)` and `close(S)` of each run, the plans' `data_cutoff`, the reports' `close(through_session)`, a standalone reconciliation's `close(S-1)`), and the rows the release could move; it passes when there is no such instant, or when no candidate row has `known_at` within 5 h after one. The reach check also covers rows dated before the window's first session (#1524): a run on the first session reads at `close(S-1)`, and a window started on a non-session day has a `started_at` before it. Keep its output, `precheck.<release>.json`, in the release's folder and its hash in the `before` record. The repair tools (`scripts/repair_stamps.py --window-earlier-ok`, `ingest --restamp-window-earlier-ok`) take that file, refuse without a pass, and check every in-window change against its instants before writing. Run the pre-check, the cache repair and the store restamp in one sitting with the paper jobs stopped (step 2): every paper run adds a read instant, and the restamp refuses a file whose instants no longer match the store. The `after` record lists the in-window changes applied, and its `sessions_to` then reaches into the window. A change that moves a stamp later, a bulk shift, an earlier move larger than the offset, or anything that touches prices, bars or actions in the window is still refused. The exam cannot move, because no decision read the row before the correction and none reads it differently after.
6. **Run it outside ingest windows and outside a running sweep.** A sweep running through a release fails its open variants with `store changed during run`.
7. **Keep every backup listed.** Each `before` and `record` entry names its backup file. If a data licence ever needs its data deleted (the deletion clause T145 will set), the list of backups is the list of files to walk. Do not delete a backup without noting it in the record (a `reason` on a new `record` entry is enough).

## The record

The store holds the record. The commands, all in the `decision data-release` group:

- `open --name N --backup <path> --reason "..."` writes the `before` row. It opens the backup read-only and refuses unless the backup holds the store's latest `ingested_at`. Only one release can be open, and a name is used once.
- `close --name N --from <session> --to <session> [--reason "..."]` writes the `after` row for the open release. Its time enters `data_vintage`, so trials that read the touched sessions go stale even when the repair only deleted rows.
- `record --name N --backup <path> --trial <id> --reason "..."` writes a closed `record` row: which backup holds the state a trial read. It prints whether the backup's vintage at the trial's cutoff equals the trial's.
- `import-file data/releases.toml` imports the legacy hand-written file once. Imported `after` rows move no vintage.

The real runs of `repair-resolution`, `repair-bars`, `repair-dividends`, `delete-master-rows` and `master-retract --apply` take `--release <name>` and refuse unless that release is open. The commands compute the numbers in the field table below, so you no longer copy them by hand.

The legacy file lives beside the store, in `data/` (gitignored, so it never reaches the public repo). Each entry was one `[[release]]` table, append-only. Its format, still what `import-file` reads (the numbers are illustrative):

```toml
[[release]]
name = "repair-13-tickers"
stage = "before"
made_at = 2026-10-10T14:05:00Z
backup_path = "data/tradepartner.duckdb.bak-20261010-1405"
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
| `name` | all | The release name. Lowercase, digits and hyphens. A `before` and its `after` share one name; record the stamped backup path separately. |
| `stage` | all | `before` (written before the repair), `after` (written after it) or `record` (a closed note about a state that already exists, such as the back-fill below). |
| `made_at` | all | When you wrote the entry. UTC, with the `Z`. |
| `backup_path` | `before`, `record` | The backup file, as a path from the project root. Leave it out on `after` (the `before` names it). |
| `store_max_ingested_at` | all | The store's latest `ingested_at` at that moment (the command below prints it). |
| `data_vintage` | all | The data vintage at `cutoff`: the latest `ingested_at` over fact rows with `known_at` on or before `cutoff`. |
| `cutoff` | all | The UTC time the vintage is computed at. For `before` and `after`: the close of `sessions_to`, read from the trading calendar (`tradepartner.calendar.session_close`), never assumed: it moves with daylight saving time and half days. For `record`: the trial's `data_cutoff`. |
| `sessions_from`, `sessions_to` | all | On `before`: the sessions you plan to touch. On `after`: the sessions the repair actually touched. On `record`: the trial's window. |
| `trial` | `record` | The trial id whose state the backup holds. |
| `reason` | all | One line: why, and the issue number. On `after`, say anything that differs from the plan. |

**A delete-only repair** (bars removed, nothing written) changes no `ingested_at` the vintage can see, so on its own it would not stale the trials that read those sessions. The `after` row `decision data-release close` writes fixes that: its time enters `data_vintage` for every cutoff on or after its `sessions_from` (`store/registry.py`). Imported legacy `after` rows move no vintage, so for a release recorded only in the TOML file, and as a belt-and-braces step after any release that deletes rows, do this: for each sweep whose trials read the touched sessions, run `sweep run <slug>` (it reruns any variant that went stale), then, once the sweep reads complete, `sweep run <slug> --rerun`, so no trial on the old rows can still select. Each rerun counts in N. Say in the `after` entry's `reason` that the release deleted rows and which sweeps you reran.

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
2. **Check the gates.** No paper window covers those sessions (once T71 starts). No ingest or `paper run` is running and none is due. No sweep is running. Stop the scheduled jobs as in [scheduling.md](scheduling.md), "Before pulling a schema migration".
3. **Back up the store.** With nothing running, check free disk first (`df -h`): the store is over 1 GB. Then:
   ```bash
   uv run tradepartner store backup
   ```
   Record the printed copy, log copy (if present) and manifest paths. Open any backup only read-only: opening it for writing may checkpoint its WAL and change the backup. The command refuses an existing target or a busy store and verifies each copy byte for byte. If the UTC minute's name already exists, wait for the next minute and rerun. Never overwrite an existing backup: it may be the only copy of a state a trial read.
4. **Open the release.** `uv run tradepartner decision data-release open --name <name> --backup <printed copy path> --reason "<why, issue number>"`. The command computes and stores the two numbers itself; "Read the two numbers" below is only for checking them by hand.
5. **Run the repair.** Every command in the batch, back to back. Dry runs first where the command has one.
6. **Check it.** `uv run tradepartner health --check` exits 0, and the checks the release's issue names pass.
7. **Close the release.** `uv run tradepartner decision data-release close --name <name> --from <first session touched> --to <last session touched>`; add `--reason` for anything that differs from the plan.
8. **Restart the jobs** (scheduling.md). The next plain `sweep run` reruns any stale variants; each rerun counts in N. If the release deleted rows, also rerun the sweeps that read those sessions as "The record" above says.

If the repair fails partway, keep the jobs stopped and nothing holding the store. First save the failed live state as evidence with `uv run tradepartner store backup` and record its printed copy, WAL and manifest paths on the release PR/issue. Then restore the pre-release backup:

```bash
BACKUP_COPY="<printed-copy-path>"
BACKUP_WAL_COPY="<printed-log-copy-path-or-empty-string>"
rm -f data/tradepartner.duckdb.wal   # else DuckDB replays the failed repair onto the restored file
cp "$BACKUP_COPY" data/tradepartner.duckdb
if [ -n "$BACKUP_WAL_COPY" ]; then cp "$BACKUP_WAL_COPY" data/tradepartner.duckdb.wal; fi
```

Use the printed WAL path even for an older backup named `<store>.wal.bak-YYYYMMDD-HHMM`. Before opening the restored store, compare the backup manifest's `wal_bytes` with the restored WAL's size (`stat -f %z data/tradepartner.duckdb.wal` on macOS). A numeric `wal_bytes` requires a WAL of that size; `null` requires no restored WAL. Stop on a mismatch.

A legacy release copy with no manifest — the hand-made `data/tradepartner.<name>.duckdb` files, such as the back-fill's `tradepartner.pre-sweep-20261008.duckdb` below — has no `wal_bytes` to compare, so skip that check: confirm no `<copy>.wal` sits beside it, open the copy once read-only to confirm it opens, then restore the copy alone with the block above, with no WAL restored.

Keep the backup itself and the failed-state copy. **A restore from a copy taken before `decision data-release open` removes that release's `before` row**, as well as all later rows; `decision data-release close` then refuses because no release is open. Record the rollback and the removed release ids on the release PR/issue. A retry is a new release with a new name. If the owner instead chooses to open and close a new release solely to record the rollback in the store, its `after` row enters `data_vintage` and stales affected trials; decide that explicitly before writing it. A full unwind also restores the paper-window state captured by the chosen backup, so verify that state before restarting any paper job.

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
