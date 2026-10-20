# Runbook: update the system, and back up or restore the store

**For:** Jose, running this yourself  ·  **Date:** 2026-10-19  ·  **Issue:** [#1938](https://github.com/josejuarez96/tradepartner/issues/1938)  ·  **Checked against:** the CLI's `--help` and `src/tradepartner/store/schema.py` (schema version 34) on `origin/main`

Two jobs on this page. Part 1 is the routine for pulling new code. Part 2 is what to do when the store file itself is damaged (disk fault, power loss, a bad copy). Both run from the main checkout, in Terminal, as you:

```bash
cd ~/Projects/tradepartner
```

Why a copy matters: any command that writes to the store upgrades it on the spot, with no prompt and no "undo". Once the store is upgraded, older code cannot read it. A copy taken before the pull is the only way back. The detail is in [scheduling](scheduling.md), "Before pulling a schema migration".

## Part 1: update the system

**When.** Pick a quiet time: between 10:30 and 16:00 ET on any day. Never between 16:00 and 21:00 ET (the ingest runs at 18:30, every day) and never between 08:00 and 10:00 ET (the paper run at 08:05 can place orders). Times are ET; if your Mac is on another time zone, convert as [scheduling](scheduling.md) says.

**1. Make sure the checkout is clean and nothing is using the store.**

```bash
git status --short          # prints nothing when the checkout is clean
git branch --show-current   # prints: main
STORE_PATH=$(uv run python -c "from tradepartner.config import get_settings; print(get_settings().store.path)")
lsof "$STORE_PATH" || echo "nothing has the store open"
```

If `git status` lists files or the branch is not `main`, stop and ask Claude. If `lsof` lists a process (a `sweep run`, a backfill, the dashboard, a job), wait for it to finish or close it, then run the last line again.

**2. See what is coming, and whether the store layout changes.**

```bash
git fetch origin
git log --oneline HEAD..origin/main                 # the changes you are about to pull
git diff HEAD origin/main -- src/tradepartner/store/schema.py | grep '^[-+]CURRENT_SCHEMA_VERSION ='
```

- **The last command prints nothing:** the store layout is unchanged. You may skip steps 3 and 4 and go to step 5. A copy is still cheap if you want one.
- **It prints two lines** (one starting `-`, one starting `+`): the pull upgrades the store on its next write. Do steps 3 and 4. For reference, the code on `main` today is at schema version 34.

**3. Stop every job, and confirm they are idle.**

```bash
launchctl list | grep com.tradepartner
```

Each line is `PID  exit-code  label`. The first column must be `-` (idle) on every line. A number there means that job is running: wait a few minutes and run the line again. Write down the labels it lists (the ingest job, the paper job, and any per-book or `collect` job you installed). Then unload all of them:

```bash
for L in $(launchctl list | awk '/com\.tradepartner/ {print $3}'); do launchctl bootout gui/$(id -u)/$L; done
launchctl list | grep com.tradepartner || echo "all jobs unloaded"
```

Unloaded jobs do not run. Your plist files are untouched.

**4. Copy the store.** Check there is room first: the store is over 1 GB.

```bash
df -h .
DAY=$(date +%Y%m%d-%H%M)
if [ -e "$STORE_PATH.bak-$DAY" ]; then echo "a copy with this name exists: wait a minute and run again"; else
  cp "$STORE_PATH" "$STORE_PATH.bak-$DAY"
  [ -f "$STORE_PATH.wal" ] && cp "$STORE_PATH.wal" "$STORE_PATH.wal.bak-$DAY"
  git rev-parse HEAD > "$STORE_PATH.bak-$DAY.commit"
  cmp "$STORE_PATH" "$STORE_PATH.bak-$DAY" && echo "copy matches"
  echo "copy is $STORE_PATH.bak-$DAY"
fi
```

`copy matches` must print. Write down the copy's name (it holds the date and time). The `.commit` file records the code you were on, which a restore needs. This copy is a plain safety copy. A copy made for a data release keeps its own name and rules ([data-releases](data-releases.md)). Never overwrite or delete a release copy.

**5. Pull and sync.**

```bash
git pull --ff-only
uv sync
```

`--ff-only` refuses if the checkout is not a simple fast-forward. If it refuses, stop and ask Claude.

**6. Load the jobs again.**

```bash
for P in ~/Library/LaunchAgents/com.tradepartner.*.plist; do launchctl bootstrap gui/$(id -u) "$P"; done
launchctl list | grep com.tradepartner
```

Every label you wrote down in step 3 must be listed again. A job you had already switched off on purpose before the update (`launchctl disable`) will refuse to load; that is fine, leave it off. The jobs run whatever code is checked out, so the next scheduled run uses the new code. Do not run `kickstart` to test it ([scheduling](scheduling.md), "Install, test, remove").

**7. Check it.**

```bash
uv run tradepartner paper status --all
uv run tradepartner health --check
```

Every book should say `last run ok` and `kill switch released`, and `health --check` should exit 0 (`echo $?` prints 0).

If a command says "a read-only connection does not migrate", that is expected after a version bump. These two commands only read, and the store is upgraded by the first write. The next scheduled ingest (18:30) or paper run (08:05) does it. Look again after that run. Do not run `paper run` or `kickstart` to force it.

**8. Look at the first runs.** After the next 18:30 ingest and the next 08:05 paper run, check both exit codes:

```bash
launchctl print gui/$(id -u)/com.tradepartner.ingest | grep "last exit code"
launchctl print gui/$(id -u)/com.tradepartner.paper | grep "last exit code"
```

Both should say 0 (what other codes mean: [scheduling](scheduling.md), "What a `paper run` exit code means"). Delete the step 4 copy only after both runs finished `ok`, with its exact name (no wildcard, and never a release copy):

```bash
rm "$STORE_PATH.bak-YYYYMMDD-HHMM" "$STORE_PATH.bak-YYYYMMDD-HHMM.commit"
[ -f "$STORE_PATH.wal.bak-YYYYMMDD-HHMM" ] && rm "$STORE_PATH.wal.bak-YYYYMMDD-HHMM"
```

(Run step 1's `STORE_PATH=` line first in a new Terminal window.)

**If the new version breaks something**, restore using Part 2, steps 1 to 5 in order (stop, keep what is there, pick the copy, restore, check). Do not skip step 2: it moves the migrated store and its log aside. Do not use `git revert` or `git reset` on `main`.

## Part 2: a store damaged outside a release

Use this when the store file is damaged by something else: a disk fault, a power cut mid-write, a failed copy, a Mac that crashed during a run. Signs: any command fails with a DuckDB error about the file, a checksum, or the write-ahead log (the `.wal` file); `health --check` cannot open the store; or the file is missing or empty.

Do not start by restoring. The damaged file may still be the best record you have.

**1. Stop writing.** Do Part 1, step 3 now (list, confirm idle, unload every job). Close the dashboard and any other session that uses the store.

**2. Keep what is there.** Move the damaged files aside; do not delete them.

```bash
STORE_PATH=$(uv run python -c "from tradepartner.config import get_settings; print(get_settings().store.path)")
DAMAGED=$STORE_PATH.damaged-$(date +%Y%m%d-%H%M%S)
mkdir "$DAMAGED" || echo "stop: that folder exists"
[ -e "$STORE_PATH" ] && mv -n "$STORE_PATH" "$DAMAGED"/
[ -e "$STORE_PATH.wal" ] && mv -n "$STORE_PATH.wal" "$DAMAGED"/
ls -l "$DAMAGED"
```

Never reuse a `damaged-` folder: it may hold the only record of what was lost.

**3. Find the newest good copy.** Look beside the store:

```bash
ls -lt "$(dirname "$STORE_PATH")"
```

Candidates, newest first: a `.bak-YYYYMMDD-HHMM` copy from Part 1, a `tradepartner.<release>.duckdb` from a data release ([data-releases](data-releases.md)), or a Time Machine copy. A `.bak-YYYYMMDD-HHMM` copy has the `.commit` file naming the code it belongs to. If there is no copy at all, stop. Ask Claude before doing anything else. The damaged files you moved aside are the only data left.

**4. Restore from your own copy.** The log file is the one trap. If a `.wal` from the damaged store stays next to the restored file, DuckDB replays it onto the copy. This block refuses to run until step 2 has moved both files away. Replace `YYYYMMDD-HHMM` with the copy's date and time, and run it in the same Terminal window as step 2:

```bash
STORE_PATH=$(uv run python -c "from tradepartner.config import get_settings; print(get_settings().store.path)")
BAK="$STORE_PATH.bak-YYYYMMDD-HHMM"
if [ -e "$STORE_PATH" ] || [ -e "$STORE_PATH.wal" ]; then echo "stop: do step 2 first"; else
  cp "$BAK" "$STORE_PATH"
  [ -f "$STORE_PATH.wal.bak-YYYYMMDD-HHMM" ] && cp "$STORE_PATH.wal.bak-YYYYMMDD-HHMM" "$STORE_PATH.wal"
  cat "$BAK.commit"
fi
```

The last line prints the commit the copy was taken under. If it differs from the commit you are on now (`git rev-parse HEAD`), and your update crossed a store-version change, check out that commit before any job loads, because newer code upgrades the store on its next write and the upgrade cannot be undone:

```bash
git checkout "$(cat "$BAK.commit")"
uv sync
```

When you decide to move forward again, run `git checkout main`, then `uv sync`, then Part 1 from step 2. A copy that was made for a data release is restored by that release's own rule ([data-releases](data-releases.md), the rollback block).

**5. Check the restored store before the jobs run.**

```bash
uv run tradepartner health --check
uv run tradepartner paper status --all
```

These only read. If they fail, put the files back from `$DAMAGED` and ask Claude.

**6. Expect a gap, and close it deliberately.**

- **Market data.** Bars and filings after the copy are missing. `health --check` lists missing sessions. Refill them with `uv run tradepartner ingest --backfill --since YYYY-MM-DD` (the date after the copy), with the jobs still unloaded. If the gap is more than a few sessions, ask Claude first: a refill stamps new `known_at` times, which can matter to trials already run.
- **Paper books.** If a book traded between the copy and the damage, Alpaca has orders the journal does not. The next paper run raises a `reconciliation` alert and halts that book, by design. Do not resume blind. Ask Claude to read the gap first ([scheduling](scheduling.md), "Kill switch").
- **Trials.** A trial run after the copy is gone from the registry. Do not re-run to "make it look the same": a re-run counts again in N. Tell Claude which trials are missing.

**7. Load the jobs again** (Part 1, step 6) only after steps 5 and 6 look right.

## NOT VERIFIED

- How DuckDB behaves on every kind of damaged file. The steps above assume only that a clean copy exists, not that a damaged file can be repaired.
- Whether a Time Machine copy of a store that was open while it was copied is consistent. Prefer your own `.bak-YYYYMMDD-HHMM` copy, which you took with the jobs stopped.
- A backup command and a backup schedule do not exist. Today the copy is by hand. A follow-up issue ([#1939](https://github.com/josejuarez96/tradepartner/issues/1939)) asks for them.
