# Runbook: scheduling the daily ingest and paper run

Owner-run. This page installs `tradepartner ingest` and `tradepartner paper run` as launchd jobs on the Mac that holds the real store, and covers the owner's operating procedure for a scheduled `paper run`: what each alert means, the kill/resume procedure, and when `abandon` is the right call. Agents never run any of this. The store, `.env` and the jobs all live in the main checkout, which agents do not enter ([teams.md](../ways-of-working/teams.md)). It also shows how to collect the evidence that closes Phase 2 plan T22: five consecutive scheduled `ok` ingest runs ([data-foundation spec](../specs/data-foundation.md), exit criteria).

The `collect` plist is a placeholder until the event collectors merge (last section).

**Status of the `paper run` section below (2026-09-30).** The CLI commands it names (`paper start|stop|run|reconcile|kill|resume|report|check|status`) are fixed by [plan task T67](../plans/paper-trading.md)'s line but not yet built (`src/tradepartner/cli.py` has no `paper` subcommand yet); each is marked **(built by T67)**. `paper abandon` and `paper override`, the `abandoned` window state and the strictly-flat rule are scheduled as a dated spec amendment by [T64b](../plans/paper-trading.md), not yet written into [the spec](../specs/paper-trading.md) or built; marked **(built by T64b/T67)**. The six `paper.*` timing keys (`submit_window_before_open_minutes` is the one that drives the sample schedule below; the rest govern the run itself) are still the spec's placeholder defaults in `config.py`, not Probe 3 measurements; [T70](../plans/paper-trading.md) sets their final values once Probe 3 (#182) reports, and this section's schedule must be recomputed then. Nothing below instructs bypassing a risk check, releasing the kill switch without a reconciliation, or putting a secret on a command line; where that is tempting there is no flag for it, by design (spec req 16).

## What the job does

Once a day, `tradepartner ingest` brings the store up to the **expected session**: the last XNYS session that had closed by `now - ingest.settle_delay_minutes` (60 by default). The trading calendar decides what a session is, so the job can run every day of the week:
- On a weekend or holiday it re-reads the last session and usually writes little beyond its run rows. EDGAR accepts filings on some market holidays, and late price revisions can land on any day.
- A run the next morning (after a missed evening, see "Sleep versus power-off") still targets the previous session.

Sources run in order, `edgar` then `alpaca`, and **the run stops at the first source that is not `ok`**. Each source that runs writes one `ingestion_runs` row, with `mode = 'session'`, except when the store is locked: a locked source writes no row, and neither does a failed source whose row write is itself locked. So a failed evening can leave both rows, only an `edgar` row, or none. The command prints one line per source and exits with:
- `0`: every source is `ok`.
- `1`: a source was stale or failed, or the store was locked.
- `2`: a configuration error before any work, such as a missing `SEC_EDGAR_USER_AGENT` or Alpaca key. The error names the variable and never prints its value.

**When to run it.** The regular close is 16:00 ET, so the expected session is that day's from 17:00 ET on (13:00 closes are handled by the calendar). The plist below runs at **18:30 local time**, which leaves margin for late bars. If the Mac's time zone is not US Eastern, convert: for example, 18:30 ET is 15:30 PT. launchd uses the Mac's local clock and follows daylight saving time with it.

## Before you install

1. **The main checkout is on `main` and clean.** The job runs whatever code is checked out there, and `uv run` syncs the environment to `uv.lock` on each run. After `git pull`, the next scheduled run uses the new code.
2. **The repo is not in a TCC-protected folder** (see TCC below). `~/Projects/tradepartner` is fine; `~/Documents`, `~/Desktop`, `~/Downloads`, iCloud Drive and external volumes are not.
3. **`.env` holds the data keys** (`ALPACA_API_KEY`, `ALPACA_API_SECRET`, `SEC_EDGAR_USER_AGENT`) and is readable only by you (`chmod 600 .env`). The settings loader reads `.env` from the project root, not from the working directory, so launchd needs nothing extra for it. Never put a secret in the plist: plists are plain text and get copied into backups.
4. **A manual run works from a fresh shell:**
   ```bash
   cd ~/Projects/tradepartner
   uv run tradepartner ingest --dry-run   # rolls every chunk back, writes no run row
   uv run tradepartner health --check
   ```
5. **Find `uv`'s absolute path:** `command -v uv`. On Apple silicon with Homebrew it is `/opt/homebrew/bin/uv`; the standalone installer puts it at `~/.local/bin/uv`.

## Before pulling a schema migration

**Any write connection migrates the store** (T49's docstring): there is no separate `migrate` command to notice running, and no prompt — the very first scheduled `ingest` or `paper run` whose code is past a schema bump just migrates the store as a side effect of its normal write. The store may already be waiting on **version 5** (T49, merged) if no write has run against it since that merge; [#365](https://github.com/josejuarez96/tradepartner/issues/365) (version 6) and [#377](https://github.com/josejuarez96/tradepartner/issues/377) (a `CHECK` enum change, also a migration) are the next ones coming. If you can't tell from the plan whether a `git pull` will bump `CURRENT_SCHEMA_VERSION`, check before pulling:

```bash
git fetch origin && git diff HEAD origin/main -- src/tradepartner/store/schema.py | grep CURRENT_SCHEMA_VERSION
```

Any output there means the pull migrates the store on its next write; if you have already pulled a version bump and are not sure whether a write has run against the store since, take the copy anyway — it costs nothing to have an extra one. Before that pull, **stop both jobs, confirm neither is mid-run, and only then copy the store file**, so a migration you need to back out of still has a clean pre-migration copy to restore from:

```bash
cd ~/Projects/tradepartner
launchctl disable gui/$(id -u)/com.tradepartner.ingest
launchctl disable gui/$(id -u)/com.tradepartner.paper   # once installed
launchctl print gui/$(id -u)/com.tradepartner.ingest | grep state   # confirm not "running"
launchctl print gui/$(id -u)/com.tradepartner.paper | grep state    # once installed; same check

STORE_PATH=$(uv run python -c "from tradepartner.config import get_settings; print(get_settings().store.path)")
cp "$STORE_PATH" "$STORE_PATH.bak-$(date +%Y%m%d)"
[ -f "$STORE_PATH.wal" ] && cp "$STORE_PATH.wal" "$STORE_PATH.wal.bak-$(date +%Y%m%d)"
git rev-parse HEAD > "$STORE_PATH.bak-$(date +%Y%m%d).commit"   # the pre-migration commit, for a restore

git pull
launchctl enable gui/$(id -u)/com.tradepartner.ingest
launchctl enable gui/$(id -u)/com.tradepartner.paper   # once installed
```

`disable`/`enable` persist the on/off state across reboots and are reversed in the same call shape used in "Install, test, remove" and "To pause the job" below; re-enabling immediately after the pull is what lets the next scheduled run actually happen and migrate the store — disabled jobs never run, so there is nothing to wait on before re-enabling. This reads the store path the running settings actually use (respecting `STORE__PATH` in `.env` if you've set it, per "PATH, working directory and `.env`" below) rather than assuming the default `data/tradepartner.duckdb`, and copies no secret — only the store file and its write-ahead log, if DuckDB has left one. Keep the dated copies until you've confirmed the next scheduled `ingest` and `paper run` both completed `ok` on the new version, then delete them.

**To restore from a copy:** disable both jobs and confirm neither is mid-run (as above), `git checkout` the commit recorded in the matching `.commit` file (the code you were on when you took that copy — code past the migration expects the new schema, so restoring the file alone is not enough), delete any live `<path>.wal` so DuckDB doesn't replay it onto the restored file, then copy the dated backup (and its `.wal.bak-DATE` counterpart, if one exists) back over the live path. Re-enable the jobs only once you've decided which code they should run next. If any window traded between the copy and the restore, expect a `reconciliation` alert on the next run until you have sorted out what the broker did in the gap — the run halts and the switch engages rather than silently losing track of an order.

## After an EDGAR cache version bump

The EDGAR adapter keeps each per-document and per-CIK cache under a versioned directory in `edgar.cache_dir`: `fsn/v{FSN_VERSION}`, `cover/v{COVER_VERSION}`, `header/v{HEADER_VERSION}`, `delisting/v{DELISTING_VERSION}`, and `stamps/` and `facts/` under `v{PARSER_VERSION}` (the constants are in `src/tradepartner/adapters/edgar_source.py`). A pull that bumps one of them makes the next run rebuild that tree under the new number. **Nothing deletes the old tree** (#615): it stays on disk until you remove it. `fsn/v1` was about 21.5 GB, and the disk filled on 2026-10-02. After the first run on the new version has finished `ok`, compare the trees on disk with the current constants, then delete only the superseded trees:

```bash
CACHE=$(uv run python -c "from tradepartner.config import get_settings; print(get_settings().edgar.cache_dir)")
uv run python -c "from tradepartner.adapters import edgar_source as e; print(f'fsn v{e.FSN_VERSION}, cover v{e.COVER_VERSION}, header v{e.HEADER_VERSION}, delisting v{e.DELISTING_VERSION}, stamps+facts v{e.PARSER_VERSION}')"
du -sh "$CACHE"/*/v*
rm -rf "$CACHE/fsn/v1"   # example: a tree whose number is below its constant
```

Never delete a tree whose number matches its constant, `failed_filings.json` (it holds your accepted failures), or `bulk/` and `index/`, which are not versioned. If you might roll back to the old code, keep the old tree until you're sure you won't.

## EDGAR input-validation list files

When the EDGAR fetch pass finds inputs that do not parse (#578), the run fails before any store write. Its message names a JSON file under `edgar.cache_dir/validation/`, `failures-<UTC stamp>.json`, which holds the full list. Every failing run writes a new file, dry runs included. When the filing-failure check fails a run with per-document failures nobody accepted, its message names a second kind of file in the same directory, `filing-failures-<UTC stamp>.json`, with every one of them (#884): the message itself lists only the first `edgar.max_validation_listed`, and a dry run writes no `failed_filings.json`. **Nothing prunes either kind** (#806): each is small, but they pile up across reruns. A run that passes the check writes nothing.

Once you have read a file and acted on it (fixed the cause, or removed the bad cached input it names), you may delete it. The run never reads these files back, so deleting one changes nothing about the next run. To list the files, then delete the ones older than 30 days:

```bash
CACHE=$(uv run python -c "from tradepartner.config import get_settings; print(get_settings().edgar.cache_dir)")
ls -lt "$CACHE/validation/"
find "$CACHE/validation" \( -name 'failures-*.json' -o -name 'filing-failures-*.json' \) -mtime +30 -print -delete
```

Delete only `validation/failures-*.json` and `validation/filing-failures-*.json` this way. Never delete `failed_filings.json` (your accepted failures) or the cache trees (see "After an EDGAR cache version bump" above).

## PATH, working directory and `.env`

launchd starts jobs with a minimal environment: `PATH=/usr/bin:/bin:/usr/sbin:/sbin`, the working directory `/`, and none of your shell profile. Three things follow:
- **`uv` by absolute path** in `ProgramArguments`, plus a `PATH` in `EnvironmentVariables` that contains `uv`'s directory, for anything `uv` spawns.
- **`WorkingDirectory` = the main checkout.** `store.path` defaults to the relative `data/tradepartner.duckdb`, so a job started anywhere else would create a new empty store there. (The EDGAR cache and `.env` are already anchored to the project root in `config.py`.) **Recommended:** also set the store's absolute path in `.env`, as `STORE__PATH=REPO/data/tradepartner.duckdb`. That removes the dependence on the working directory for the job, manual runs and the evidence query alike. Keep it in `.env`, not in the plist.
- **`.env`**: see step 3 above. `TRADEPARTNER_ENV_FILE` overrides its path for an alternate environment; the scheduled job must not set it. Process environment variables beat `.env`, and values set with `launchctl setenv` reach every job, so check that both of these print nothing:
  ```bash
  launchctl getenv TRADEPARTNER_ENV_FILE
  launchctl getenv STORE__PATH
  ```

## The ingest plist

Save the plist as `~/Library/LaunchAgents/com.tradepartner.ingest.plist`. Replace `REPO` with the checkout's absolute path, `HOME_DIR` with your home directory, and the `uv` path if yours differs. launchd does not expand `~` or `$HOME` inside a plist.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.tradepartner.ingest</string>

    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/caffeinate</string>
        <string>-i</string>
        <string>/opt/homebrew/bin/uv</string>
        <string>run</string>
        <string>--frozen</string>
        <string>tradepartner</string>
        <string>ingest</string>
    </array>

    <key>WorkingDirectory</key>
    <string>REPO</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>18</integer>
        <key>Minute</key>
        <integer>30</integer>
    </dict>

    <key>RunAtLoad</key>
    <false/>

    <key>StandardOutPath</key>
    <string>HOME_DIR/Library/Logs/tradepartner/ingest.log</string>
    <key>StandardErrorPath</key>
    <string>HOME_DIR/Library/Logs/tradepartner/ingest.err.log</string>
</dict>
</plist>
```

Notes on the choices:
- **A LaunchAgent, not a LaunchDaemon.** It runs as you, with your `.env` and your file permissions, whenever you are logged in; a locked screen is fine. A LaunchDaemon would run as root, which this job must not do.
- **`caffeinate -i`** holds off idle sleep for as long as the command runs. Without it, a Mac woken by the schedule can go back to sleep in the middle of the EDGAR pass. It keeps nothing awake after the command exits.
- **`uv run --frozen`** uses `uv.lock` as it is and never rewrites it.
- **No `KeepAlive`, no retry.** A failed run is visible in the log and in `health --check`, and the next evening's run starts over. Ingest is idempotent: a rerun writes only what changes an as-of read.

## Install, test, remove

```bash
mkdir -p ~/Library/Logs/tradepartner
plutil -lint ~/Library/LaunchAgents/com.tradepartner.ingest.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tradepartner.ingest.plist

# run it once now instead of waiting for 18:30, then read the result
launchctl kickstart gui/$(id -u)/com.tradepartner.ingest
launchctl print gui/$(id -u)/com.tradepartner.ingest | grep -E "state|last exit code|runs"
tail -n 20 ~/Library/Logs/tradepartner/ingest.log ~/Library/Logs/tradepartner/ingest.err.log
```

A `kickstart` run is a manual run. It writes real rows but does not count toward the scheduled evidence.

- **Check that macOS allows it.** Since macOS 13, an agent in `~/Library/LaunchAgents` appears under System Settings > General > Login Items > "Allow in the Background". If it is switched off there, the job silently never runs.
- **After editing the plist:** `bootout`, then `bootstrap` again. launchd does not reread a loaded plist.
- **To pause the job** (for example during a long `ingest --backfill`: the backfill releases the lock between chunks and ingest retries for `store.lock_retry_seconds`, but that evening's run may still end `locked`):
  ```bash
  launchctl disable gui/$(id -u)/com.tradepartner.ingest   # persists across reboots
  launchctl enable gui/$(id -u)/com.tradepartner.ingest    # resume
  ```
- **To remove it:** `launchctl bootout gui/$(id -u)/com.tradepartner.ingest`, then delete the plist file.

## Sleep versus power-off

A `StartCalendarInterval` job behaves differently depending on the Mac's state at 18:30:

| Mac at 18:30 | What happens |
|---|---|
| Awake | Runs at 18:30. |
| Asleep | Runs when the Mac next wakes. Several missed evenings coalesce into **one** run on wake, and that run fetches bars for the latest expected session only. The skipped sessions stay missing until a backfill fills them: after a sleep spanning more than one session, run `health --check`, and if it reports missing sessions, run `uv run tradepartner ingest --backfill --since DATE` (pause the job first, see above). |
| Powered off | **The run is skipped.** Nothing runs at the next boot. |
| Logged out | The job does not run (LaunchAgents need your login session). |

A scheduled wake shortly before 18:30 makes the sleeping case run on time. This needs admin rights, so run it yourself in Terminal:

```bash
sudo pmset repeat wake MTWRFSU 18:28:00
pmset -g sched          # confirm
```

- **Use `wake`, not `wakeorpoweron`.** Powering on a Mac that is off stops at the login screen (or the FileVault unlock), where no LaunchAgent runs, so it only gives a false sense of coverage. Leave the Mac asleep, not off.
- **The wake is two minutes before the job** so the Mac has little time to idle back to sleep first. `caffeinate -i` in the plist then keeps it awake for the run.
- **`pmset` keeps a single repeating wake event**, so this replaces any existing one.

Also keep the Mac on power:
- **A laptop on battery with the lid closed** may not wake fully for a scheduled event. Leave the lid open on power, or use it in clamshell mode with a display and power attached.
- **Check the first week's log.** A run that started without network, because Wi-Fi had not reconnected yet, fails its sources and shows it there. If that happens, move the wake earlier.

## Logs

- **The job's output.** `~/Library/Logs/tradepartner/ingest.log` receives the per-source result lines. `ingest.err.log` receives tracebacks and warnings. Both grow by a few lines a day, so no rotation is needed; truncate them when you like.
- **launchd's view.** `launchctl print gui/$(id -u)/com.tradepartner.ingest` shows the last exit code and the run count. A job launchd could not start, because of a bad path or a `WorkingDirectory` that does not exist, shows up there and in `log show --last 1d --predicate 'process == "launchd"' | grep tradepartner`.
- **The store's view.** The `ingestion_runs` rows, the data-health page (`uv run tradepartner dashboard`), and `uv run tradepartner health --check`. `health --check` exits 1 on a failed integrity rule. It also warns when the latest EDGAR run reports quarantined filings.
- **Secrets in the logs.** The printed result lines and the stored run messages have the data keys redacted, and a configuration error names a missing variable, never its value. **Tracebacks in `ingest.err.log` are not redacted.** One raised outside a source's run (settings validation, adapter construction) prints the exception as Python formats it. These normally carry URLs, not headers, but treat `ingest.err.log` like `.env` and don't paste it anywhere unread.
- **If a job ever posts to an issue** (no job does today): it posts one scrubbed status line, the run id, the source and the outcome, and never command output, a log line or a traceback (#773).

## TCC (macOS privacy protection)

macOS blocks processes started by launchd from reading these locations unless the binary has been granted access:
- `~/Documents`, `~/Desktop`, `~/Downloads`
- iCloud Drive
- removable and network volumes

A blocked run fails, even though the same command works in Terminal, because Terminal holds its own grant. It shows up in one of two places:
- **A blocked file read** shows as `Operation not permitted` in `ingest.err.log`.
- **A blocked `WorkingDirectory`** makes launchd fail the spawn. It then shows only as a non-zero last exit code in `launchctl print`, and nothing reaches the log. The fix is to keep the checkout, the store and the logs outside those folders (`~/Projects/...`, `~/Library/Logs/...`). Do not grant Full Disk Access to `uv` or Python to work around it: that grant covers every script those binaries ever run.

## Collecting the T22 evidence

After the job has been installed for at least a week, read the recent session runs. The query opens the store the settings name (`STORE__PATH` included) through one short read-only connection. It holds that connection only for the read, and ingest's writer retries for `store.lock_retry_seconds`, so it never blocks a run. If ingest holds the store at that moment, the query fails at once with a lock error; rerun it a minute later.

```bash
uv run python - <<'PY'
from tradepartner.config import get_settings
from tradepartner.store.db import open_read_only

with open_read_only(get_settings()) as con:
    rows = con.execute("""
        SELECT started_at AT TIME ZONE 'America/New_York' AS started_et,
               source, status, chunk_cursor, rows_added
        FROM ingestion_runs
        WHERE mode = 'session'
        ORDER BY started_at DESC
        LIMIT 20
    """).fetchall()
for r in rows:
    print(*r)
PY
```

The evidence is **five consecutive scheduled runs in which both `edgar` and `alpaca` rows are `ok`**. Two things make a run count:
- **Scheduled:** its `started_et` is at 18:30, or at a wake time the log explains. That tells it apart from your manual runs.
- **Consecutive:** no scheduled run between them failed. A failed run can show a row that is not `ok`, only an `edgar` row (the run stopped before `alpaca`), or no row at all (locked), so also check `ingest.log` for every evening in the stretch.

Five runs over five distinct trading sessions (`chunk_cursor`) is the stronger reading, and the one to aim for. Paste the query output and the matching `ingest.log` lines into the T23 close-out PR, which carries the T22 evidence; T22 is ticked when that evidence is in.

## Scheduling `paper run`

Install this as part of [T71](../plans/paper-trading.md), right after `paper start` succeeds on the real store — T71's own evidence list includes "the plist installed." Before that, there is no open window for the job to act on anyway: a `paper run` with no window open just writes a `no_window` alert and exits (see the alert table below), so there is nothing to gain by installing or running it earlier.

### What the job does

Once per XNYS session, `tradepartner paper run` **(built by T67)** always collects order outcomes since the last run, reconciles the ledger against the broker, and marks the account (drawdown check); while the kill switch is engaged it still does this much, read-only against the broker, but submits nothing. Whether it also submits orders depends on the session and the window, not only on whether S is a fill session:
- **A fill session, run starting inside the submit window:** plans, submits sells, waits for them to finish, then submits buys sized from the cash the sells raised.
- **A fill session, run starting outside the window:** leaves the rebalance `pending` for a later in-window run; nothing is submitted, forced exits included.
- **A catch-up session** (S still within `paper.max_catch_up_sessions` of the last fill session F_i) and **forced exits inside the submit window on a non-fill session**: these can also submit orders. **A `stop` run (after `paper stop --reason`) can also liquidate, inside the submit window, on any session, fill or not.** `paper abandon` is different: it places and cancels no order itself (see "`abandon`" below) — after an abandon, a `paper run` just exits `no_window`.

So "outside the submit window" is the time-based condition under which a `paper run` is guaranteed to submit nothing (an engaged kill switch is the other: see above); see the kickstart warning below.

**The submit window** is `[open(S) − paper.submit_window_before_open_minutes, open(S) + paper.submit_window_after_open_minutes]`. With the current config defaults (`submit_window_before_open_minutes=90`, `submit_window_after_open_minutes=30`, `sell_wait_seconds=900`, `poll_interval_seconds=15`, `accept_wait_seconds=30`) the window opens 90 minutes before the open and closes 30 minutes after it (T70 checked all six keys against Probe 3's measured fill latency and kept them, #294; they are run-time keys and may still move with a dated note in the spec). Recompute the schedule below against whatever `paper.submit_window_before_open_minutes` is on main at the time you install the job (`uv run python -c "from tradepartner.config import get_settings; print(get_settings().paper)"`).

The job must start **after the previous session's `ingest` has completed** (the scheduler assumption in the spec's "Users & usage") and **inside the submit window**, so schedule it a few minutes after the window opens: that gives margin for `uv sync` and the clock pre-check without eating into the window reserved for sells-then-buys. With today's placeholder defaults (open at 9:30 ET, window opens at 8:00 ET) that means roughly **08:05 ET**; the 18:30 ET ingest job from the previous evening is long finished by then, unless that evening's run itself ran late or coalesced on a wake (see "Sleep versus power-off" above) — a coalesced ingest finishing after 08:05 halts that day's paper run with `stale_data` instead; the kill switch does not engage for it, and on a fill session the rebalance stays `pending` for catch-up.

**Wake coverage for two jobs.** `pmset repeat` holds only one repeating wake event (see "Sleep versus power-off" above), and the ingest job already claims the evening one (18:28 ET). Scheduling a second `pmset repeat wake` entry for the paper job's morning start **replaces** that evening entry, not adds to it. Rather than juggle two wake times, keep the Mac on power without sleeping overnight once the paper job is installed (Settings > Energy, "Prevent automatic sleeping on power adapter"); `caffeinate -i` in each plist is then enough to survive the run itself. If you still prefer letting the Mac sleep, pick one `pmset repeat wake` time that comfortably precedes both jobs' needs and accept that a wake timed only for the evening ingest can leave the morning paper job starting late or outside the submit window.

### The paper plist

Save as `~/Library/LaunchAgents/com.tradepartner.paper.plist`, beside the ingest one. Same `REPO`, `HOME_DIR` and `uv` substitutions as above. `StartCalendarInterval` is the Mac's **local clock**, same as the ingest plist: 08:05 ET is the literal Hour/Minute below only if the Mac's time zone is US Eastern; otherwise convert (see "When to run it" above) and follow daylight saving time with the local clock as the ingest section does.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.tradepartner.paper</string>

    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/caffeinate</string>
        <string>-i</string>
        <string>/opt/homebrew/bin/uv</string>
        <string>run</string>
        <string>--frozen</string>
        <string>tradepartner</string>
        <string>paper</string>
        <string>run</string>
    </array>

    <key>WorkingDirectory</key>
    <string>REPO</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
        <key>TRADEPARTNER_INVOKED_BY</key>
        <string>scheduler</string>
    </dict>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>8</integer>
        <key>Minute</key>
        <integer>5</integer>
    </dict>

    <key>RunAtLoad</key>
    <false/>

    <key>StandardOutPath</key>
    <string>HOME_DIR/Library/Logs/tradepartner/paper.log</string>
    <key>StandardErrorPath</key>
    <string>HOME_DIR/Library/Logs/tradepartner/paper.err.log</string>
</dict>
</plist>
```

Everything from "PATH, working directory and `.env`" above applies unchanged: `uv` by absolute path, `WorkingDirectory` the main checkout (so `STORE__PATH` resolves the same as for ingest), `.env` read from the project root, and no secret in the plist — the two `ALPACA_PAPER_API_*` keys and the four `ALERT_*` SMTP settings stay in `.env`, never here and never typed on a command line.

`TRADEPARTNER_INVOKED_BY=scheduler` combined with a non-interactive `stdin` makes the run's `invoked_by` field `scheduler`; only a `scheduler`-invoked, successfully executed rebalance counts toward `paper.min_rebalances` for the phase exit check (`paper check`). **A `launchctl kickstart` run is also `invoked_by=scheduler`, not `tty`**: kickstart starts the job under launchd with the plist's own `EnvironmentVariables` and a non-TTY stdin, exactly like the scheduled run, so it counts toward the exit-criteria evidence the same way. Only a run you start yourself from an interactive Terminal (no `TRADEPARTNER_INVOKED_BY`, a real TTY) is `invoked_by=tty` and does not count — the opposite of the ingest evidence's scheduled-vs-manual split, where `kickstart` does *not* count. **Never `kickstart` the paper job inside the submit window** just to test it; it can submit real paper orders (a fill session, a catch-up session, or forced exits) and any trade it makes counts toward the exit criteria. Use `uv run tradepartner paper status` to check state, or a manual Terminal `tradepartner paper run` outside the window, instead.

Install, test and pause it exactly as for ingest ("Install, test, remove" above), substituting `com.tradepartner.paper` and `paper.log`/`paper.err.log`:

```bash
mkdir -p ~/Library/Logs/tradepartner
plutil -lint ~/Library/LaunchAgents/com.tradepartner.paper.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tradepartner.paper.plist

launchctl kickstart gui/$(id -u)/com.tradepartner.paper
launchctl print gui/$(id -u)/com.tradepartner.paper | grep -E "state|last exit code|runs"
tail -n 40 ~/Library/Logs/tradepartner/paper.log ~/Library/Logs/tradepartner/paper.err.log
```

As above, a `kickstart` run **counts as `scheduler`-invoked**, the same as a real scheduled run, and ("What the job does" above) can submit real paper orders on a fill session, a catch-up session, or for forced exits — on any session, inside the submit window. Prefer `uv run tradepartner paper status` **(built by T67)** to check state without placing anything; reserve `kickstart` for a time **outside the submit window**, or for when you deliberately want a scheduler-attributed run.

### Several books: one plist, each book's run time, `--book`

([ADR 0017](../decisions/0017-fast-paper-and-machine-readiness-gate.md) part B; plan T155b.) A **book** is a token (default `paper.book_id`, `main` for H1) with its own Alpaca paper account, key pair, run lock and at most one open window. The plist above needs no change for a second book: `tradepartner paper run` with **no `--book`** runs every book that has an open window, **in token order**, each its own run under its own lock and its own `paper_runs` row. A crash in one book's run is that book's `crashed` entry and never stops the next one. With one open book (`main`) it is exactly the run described above. A `paper run` with no open book at all is `paper.book_id`'s run alone and writes its `no_window` alert as before.

The job's exit code is the worst of the books': 3 (`WRITE_FAILED_EXIT_CODE`) if any book's halt path could not write its switch, else 1 if any book's run exited non-zero, else 0. One line per book is printed, so read `paper.log` per book, not only `last exit code`. A `locked` book (another `paper run`, `resume`, `reconcile` or `stop` holding that book's lock) is that book's line and exit; the other books still run.

**Every book's run time against the submit window.** The books run one after another under one job, so a later book starts after the earlier ones finish, and a run that waits for its sells (`sell_wait_seconds`, up to 900 s with today's defaults) pushes every later book toward the close of the window (`submit_window_after_open_minutes`). **From the first week with two or more books, record each book's run time** from the journal and compare it with the window. This is a read-only query on the real store, run by the owner (never on a copy an agent made, never while a run is in flight):

```bash
uv run python - <<'PY'
import duckdb
from tradepartner.config import get_settings

con = duckdb.connect(str(get_settings().store.path), read_only=True)
print(con.sql("""
    SELECT w.book_id, r.session, r.kind, r.started_at, x.finished_at, x.status
    FROM paper_runs r
    JOIN paper_windows w USING (window_id)
    LEFT JOIN paper_run_results x USING (run_id)
    WHERE r.session IS NOT NULL
    ORDER BY r.session DESC, r.started_at
    LIMIT 40
"""))
PY
```

Convert `started_at` to New York time and read it against `[open(S) - paper.submit_window_before_open_minutes, open(S) + paper.submit_window_after_open_minutes]`. A fill session is the one that matters: a book whose `started_at` falls after the window closed leaves its rebalance `pending` for catch-up with nothing submitted ("What the job does" above). Keep the table for the Phase 4 retro.

**The fallback: one plist per book, staggered.** Only once a book has been seen to miss the window (its `started_at` outside it, or a `missed_run` alert, or a catch-up for a rebalance that should have traded on its session), split the job. For each book, copy the paper plist to `com.tradepartner.paper.<book>.plist` and change three things: the `Label` (`com.tradepartner.paper.<book>`), the arguments (add `<string>--book</string>` and `<string>BOOK</string>` after `<string>run</string>`), and the log names (`paper.<book>.log`, `paper.<book>.err.log`). Give each a different `StartCalendarInterval` Minute, a few minutes apart and all inside the window with room for the earlier book's sells (for example `main` at 08:05 and the next book at 08:20), and **remove the all-books job** (`launchctl bootout gui/$(id -u)/com.tradepartner.paper`) so no book is run twice. Install, test and pause each as above. The run lock is per book, so the staggered jobs cannot step on each other; two jobs for the same book would just produce a `locked` alert.

**`--book` on every command.** Every `paper` command except `shakedown` takes `--book <token>`, default `paper.book_id` (`main`): `start`, `stop`, `run`, `reconcile`, `kill`, `resume`, `report`, `check`, `status`, `abandon`, `override` and `settle`. A token outside the grammar is a usage error (exit 2) before the store is opened. `--book` picks a book's paper key pair, never an endpoint. Examples:

```bash
uv run tradepartner paper status --all              # one summary line per book
uv run tradepartner paper status --book <token>     # one book in full
uv run tradepartner paper kill --book <token> --reason "why"
uv run tradepartner paper kill --all --reason "why"  # every book with an open window
uv run tradepartner paper resume --book <token> --reason "root cause and what you checked"
uv run tradepartner paper check --book <token>
```

A kill is per book (the switch is derived per window); `--all` is the owner's all-books stop. Resume is always per book and always reconciles first. Every other procedure on this page (alerts, exit codes, resume, `abandon`) applies to a book with `--book <token>` added.

### Checking a run

After a scheduled run, before moving on with your day:

```bash
uv run tradepartner paper status        # (built by T67) prints the operations-page numbers
```

This shows: the store's "as of" and the run's "last updated" (with a stale chip if S−1 has no run row), the four KPIs including the kill-switch chip, today's plan ranking, fills, the per-order journal chain, alerts, and reconciliation status. Read this, or the operations page, most days — it is the one thing the spec asks the owner to do routinely.

### What a `paper run` exit code means

`launchctl print gui/$(id -u)/com.tradepartner.paper | grep "last exit code"` and `paper.err.log` are what you have when the job ran unattended; the exit code alone tells you which of these happened:

| Exit code | Meaning | What you do |
|---|---|---|
| 0 | The run ended `ok`, `no_session` or `skipped_kill_switch`. | Nothing; this is routine. |
| 1 (`CRASH_EXIT_CODE`) | An uncaught exception (`run_failed`/`failed`), a halt (`halted`/`stale_data`), or a run that ended `locked`/`no_window`. | Read `paper.err.log` and the alerts list (`paper status`), per "What each alert kind means" below. A power-loss or killed-process crash may show a different, signal-related code (or none at all, if launchd never saw it exit) rather than exactly 1 — in that case there is no `paper_run_results` row for S−1 and no alert either; `paper status` shows the switch engaged, and the next run closes it `crashed`. |
| 2 | The CLI's usage error (`cli.USAGE_ERROR`): a bad flag or argument, refused before any run started. Unrelated to the paper run itself. | Fix the plist/command line; re-test with a manual `uv run tradepartner paper run` outside the window. |
| 3 (`WRITE_FAILED_EXIT_CODE`) | The halt path could not write the `kill_switch` `engaged` row itself — the store may be unreliable. Deliberately distinct from both 1 and 2 (#366 Q22 (ii)) so this one case is visible from `launchctl print` alone, before you even open a log. | Stop and check the store/disk before anything else; see the `kill_switch_write_failed` row below. Do not resume until you've confirmed the store is healthy. |

`CRASH_EXIT_CODE` and `WRITE_FAILED_EXIT_CODE` live next to each other in `src/tradepartner/execution/wrapper.py`; `USAGE_ERROR` is in `src/tradepartner/cli.py`.

### What each alert kind means, and what you do

Every alert is a row in the `alerts` table first (the source of truth), delivered also to `macos` and `email` if `alerts.channels` and, for email, the four `ALERT_*` variables are set — except `kill_switch_write_failed` (see its row below), which never reaches the store and goes out only through the other channels. `paper status` and the operations page list stored alerts. `alerts.channels` must include at least one of `macos`/`email` (enforced at config load, #366 Q22 (iii)), precisely so a `kill_switch_write_failed` alert always reaches you even though it never touches the store.

| Alert kind | What happened | What you do |
|---|---|---|
| `halted` | A system fault (bad data, broker error, clock fault, …) stopped the run partway and engaged the kill switch. | Read the alert message and `paper.err.log` for the underlying exception. Fix the cause (network, broker outage, a bad `.env` value), then follow the resume procedure below. |
| `run_failed` | Any other exception the run didn't classify as a specific fault; the run's result is `failed`. | Same as `halted`: read `paper.err.log`, fix the cause, then resume. A crash (power loss, process killed) is different: the `paper_runs` row was already written, so there is no `missed_run` for it, and the crash itself raises **no alert at all** — the next run instead closes it as `crashed`, `paper status` shows the switch engaged, and that next run ends `skipped_kill_switch` until you resume. This is why "Checking a run" above is routine, not just something you do when an alert tells you to. |
| `stale_data` | Ingest hasn't reached S−1 for the reference symbol yet. | Check the ingest job ran and was `ok`. This does **not** engage the kill switch — once ingest catches up, the next `paper run` proceeds on its own; no resume needed. |
| `kill_switch` | The switch's engaged/released state changed (a halt, a drawdown trip, your `kill`, or a release). | Confirm which: `paper status` shows current state and the triggering source. |
| `kill_switch_write_failed` | The kill-switch row itself couldn't be written — the store may be unreliable. | Delivered outside the store (macOS/email only, since the store can't be trusted). Stop and check the store/disk before anything else; do not resume until you've confirmed the store is healthy. |
| `reconciliation` | The ledger and the broker disagree on a mismatch `reconcile_now` couldn't explain. | Read the mismatch in `paper status`. This halts and engages the switch; see Kill/resume below. |
| `rejection_cap` | Too many of this run's orders were rejected by the broker (or all of them were). | Check why the broker is rejecting (symbol trading halted, account restriction, bad request shape). The switch is engaged; investigate before resuming. A later run's collection can also surface this for an earlier run's orders. |
| `skip_cap` | Too many per-name skips in one rebalance (other than `dust`/`untradable`). | `SkipCapError` is a system fault like any other: it halts the run and **engages the kill switch**. Read the skip reasons in the plan/decisions view, follow the resume procedure below, then the rebalance stays `missed` reason `skip_cap` — it is not retried until the next rebalance event. |
| `missed_run` | No `paper_runs` row exists for S−1 — a scheduled run never happened. | Check whether the Mac was off (not asleep) at the scheduled time, or the job was disabled. See "Sleep versus power-off" and "Wake coverage for two jobs" above; both apply to this job too. |
| `missed_rebalance` | A rebalance event was logged `missed`, reason one of `catch_up_lapsed`, `kill_switch`, `limit_breach`, `skip_cap` or `window_stop`. | Read the reason in the alert message; it names which of the above stopped this rebalance from trading. |
| `drawdown` | Ledger equity fell more than `risk.max_drawdown` below the window's peak. | This engages the kill switch automatically (a risk rule, not a judgment call). `paper stop` is refused while the switch is engaged, so you cannot go straight to stopping: `paper resume --reason "..."` first (which also resets the drawdown peak to the ledger equity at the last mark — a further drop from the *new*, lower peak can trip it again), and only then decide whether to let trading continue or `paper stop --reason "..."` right away. |
| `unspent_cash` | After buys executed, leftover cash exceeded `risk.max_unspent_cash_fraction` of equity. | Informational; check the plan's sizing on `paper status`. Does not engage the switch. |
| `locked` | A second instance found the run lock (`<store.path>.paper.<book>.lock`, per book; book `main` also takes the pre-book `<store.path>.paper.lock`) held — by another `paper run`, or by a `resume`, `reconcile` or `stop` in progress. Ingest uses a separate store lock and is not a cause of this one. | No `paper_runs` row was written for this attempt; the lock holder's own run proceeds normally. If this was the day's only scheduled attempt, expect `missed_run` for S−1 on the next run, and on a fill session the rebalance falls to catch-up. If it recurs, check for a stuck process holding the paper lock, and avoid running `resume`/`reconcile`/`stop` right at the scheduled start time. |
| `no_window` | `paper run` ran with no open window; `kill`, `resume`, `reconcile`, `abandon` and `override` are refused the same way but write no alert of their own. | Normal before the first `paper start` or after `paper stop`. If you expected a window to be open, check `paper status`. Once the final window has closed for good, `launchctl bootout` the paper job so it stops alerting daily. |

### Kill switch: engage, and the resume procedure

**Engaging it yourself**, outside an automatic halt or drawdown trip:

```bash
uv run tradepartner paper kill --reason "why you are stopping trading"   # (built by T67)
```

`--reason` is required; there is no flag to engage it silently. While engaged, the next scheduled `paper run` still collects outcomes, reconciles and marks (read-only against the broker) but submits nothing — a pending rebalance waits, and is traded after release if the catch-up window still allows it, otherwise logged `missed` reason `kill_switch`.

**Never release the switch without reconciling first.** You don't get a choice about this: `paper resume` runs reconciliation as one of its own steps and refuses to append `released` if it fails — there is no flag that skips it. `--reason` is required, and resume itself is refused with `no_window` when no window is open. In order, `paper resume --reason "..."`:
1. Takes the run lock, journals a `resume_invocations` row, and closes any unfinished (crashed) run.
2. **Settles every order a crash left `pending`**: reads it back from the broker and journals its real status, or marks it `cancelled` (`not_received`) if the broker never saw it. This is "a release settles any order a crash left pending" from the spec's Users & usage section — it happens automatically, you do not do anything extra for it.
3. Collects every non-terminal order.
4. Without `--accept-broker-fills`, refuses outright while any order is `fills_lagging` past `risk.max_fill_lag_sessions` (see below).
5. Runs reconciliation and **refuses to proceed if it fails** — the alert stays, the switch stays engaged, and you need to find out why before trying again.
6. Appends the `released` row.

```bash
uv run tradepartner paper resume --reason "root cause and what you checked"   # (built by T61b/T67)
```

**When `--accept-broker-fills` is the right call.** Normally omit it. Add it only when:
- An order has been `fills_lagging` for `risk.max_fill_lag_sessions` or more sessions (the spec's bound) — the journal's fill total is behind what the broker's `get_order` reports, and the fill-stream has never caught up on its own, **and**
- You have checked `paper status`/the reconciliation view and confirmed the broker reports that specific order as **finished** (not still open) — an order the broker still holds open is refused even with the flag, since it may yet fill through the normal path.

```bash
uv run tradepartner paper resume --accept-broker-fills --reason "order <id>: broker reports filled, feed never delivered it after N sessions"   # (built by T61b/T67)
```

With the flag, for each qualifying order `paper resume` journals a synthetic fill for the residual quantity only (broker's `filled_quantity` minus what's already journaled), priced from the implied residual and flagged `price_implied`. **The synthetic fill is the one that stands**: if a real fill for that order arrives later anyway, it is journaled `superseded_by` the synthetic one and every reader filters it out — the implied price is not retroactively corrected to the real one. Without the flag, resume refuses outright while any such order exists; this is the owner's considered decision to trust the broker's number over a missing feed message, not something to reach for reflexively because resume is otherwise refusing.

### The kill-switch drill (ADR 0017 part E.4)

The drill proves, on the real store, that an engaged switch stops a scheduled run from submitting anything. It is one of the seven shakedown lines (E.4) and is run **once, on book `main`, on an H1 fill session** inside the shakedown span (the first session after a month-end; not H1's first fill session, which is the evidence run and must trade). `main` hosts it because it is the fastest book that is not a forward exam: the drill costs H1 at most one rebalance (delayed to a catch-up run, or `missed` with reason `kill_switch` if the catch-up lapses), reported by `paper report` and never an exam result. The owner times it.

1. **Before the scheduled run, on the fill session** (after the previous session's ingest, before the job's start, 08:05 ET with today's plist), engage the switch yourself:
   ```bash
   uv run tradepartner paper kill --book main --reason "drill"
   ```
   The `--reason` is journaled; the `kill_switch` row it writes has `state = engaged` and `source = owner`, which is what E.4 reads. Confirm with `uv run tradepartner paper status --book main` that the switch shows engaged. Engage it **only** for `main`: `--all` would drill every book and cost each its rebalance.
2. **Let the scheduled run skip.** Do not start the run yourself and do not `kickstart` it: the line needs a run with `invoked_by = 'scheduler'` (and a kickstart inside the window can trade if the switch is not engaged). The job still collects, reconciles and marks, and submits nothing; `main`'s result is `skipped_kill_switch` (exit 0) with **no `orders` row**. Check it in `paper status --book main` after the run. Any other book with an open window runs normally in the same job.
3. **Resume the same day**, once the run has finished (never before it, or the run trades):
   ```bash
   uv run tradepartner paper resume --book main --reason "drill complete: <what you checked>"
   ```
   Resume settles, collects, reconciles and appends the `released` row, exactly as in "Kill switch" above; if reconciliation fails it refuses and the switch stays engaged, so find out why before trying again. The `released` row is the third thing E.4 reads. The drilled rebalance trades on a later catch-up run if the catch-up window allows it, else it is logged `missed` (reason `kill_switch`).

Nothing in the drill bypasses a risk check or releases the switch without a reconciliation. If the drilled session shows an `orders` row for `main`, or the run's result is anything but `skipped_kill_switch`, stop and treat it as an incident: that is a kill-switch defect, not a drill result.

### The shakedown: the span, notes and `paper shakedown`

The Phase 4 exit is [ADR 0017](../decisions/0017-fast-paper-and-machine-readiness-gate.md) part E: `tradepartner paper shakedown` exits 0 on the real store with all seven lines passed over every book, and the Phase 4 retro records the output.

**Open the span** with one owner decision:

```bash
uv run tradepartner decision shakedown-span --sessions 10 --order-sessions 5 --reason "..."
```

`--sessions N` is how many sessions the span needs and `--order-sessions M` how many of them need a live fill (decided: N = 10, M = 5; a longer span keeps one order session per two sessions). The `--reason` names the strategy that goes live first, from its exam of record (ADR 0017 open question 8). The span is every XNYS session from the session after the row's time through the last completed session. The thresholds are read from that row, never from `config.py` or `.env`, so the bar cannot move after a failing span. A new `shakedown-span` row restarts the span, and the rerun is recorded. Open it so that an H1 fill session lies inside the ten (the drill needs one).

**A `mismatch` reconciliation or a real halt inside the span restarts it**: the span then starts at the session after the `released` kill-switch row that followed it. Fix the cause and resume; open a new `shakedown-span` row only if you want the count to start later.

**Annotate an alert** that the span would otherwise fail on, by the alert's id as `paper status` lists it (a `stale_data` or `missed_run` alert):

```bash
uv run tradepartner decision shakedown-note --alert <alert id> --reason "what happened and why it is accepted"
```

A note counts for E.1 (a `stale` run, or a session with no run row, passes with a note on its alert; the line prints how many passed by note) and for E.7 (a `stale_data` alert needs one). An unknown alert id is refused. A note excuses late data only; it does not excuse a mismatch or a halt.

**Run the gate** (read-only, writes nothing; exits 0 only when every line passes, and 1 when any fails or no `shakedown-span` row exists):

```bash
uv run tradepartner paper shakedown
```

It prints one line per criterion with the rows it read, its query, the thresholds and what it found. The seven lines (ADR 0017 part E, criteria 1 to 7):

| Line | What passing means |
|---|---|
| E.1 sessions | For every session of the span and every book whose window was open, a scheduler-invoked run ended `ok`, or `skipped_kill_switch` during the drill, or `stale` with a note (a session with no run passes only with a note on its `missed_run` alert); and on at least M sessions, across all books, a scheduler-invoked run submitted an order with a live fill. The span must also hold at least N completed sessions. |
| E.2 reconciliation | No `mismatch` reconciliation in any book; `pending_unresolved` and `fills_lagging` only when that book's next reconciliation is `ok`. |
| E.3 orders | No decision whose live fills exceed its planned quantity or notional plus the frozen tolerance, no order whose fills exceed its own size plus the tolerance, and no run that ended in a `LimitBreachError`. |
| E.4 kill-switch drill | In at least one book: an owner `engaged` row, then a scheduler run of kind `rebalance` or `catch_up` that ended `skipped_kill_switch` and wrote no `orders` row, then a `released` row (the drill above). A real halt in the span is printed beside it but restarts the span once resumed. |
| E.5 journal | `paper check`'s `chain` and `override_reason` lines pass for every book: every due chain is complete and no live fill is known after its terminal event. |
| E.6 alerts | At least one `alert_deliveries` row with `ok = TRUE` in the span for every non-store channel in `alerts.channels` (`macos`, `email`). |
| E.7 data | Every `stale_data` alert in the span has a note, and `tradepartner health` passes on the day the command runs. |

A failing line is not a reason to edit a threshold: fix the cause, note the alert, or restart the span.

**Where the evidence is kept.** Paste the seven lines of the passing run on #1352 and copy them into the Phase 4 retro (under `docs/retros/`, written at the phase close), together with the per-book run-time table from "Several books" above and the span's `shakedown-span` and `shakedown-note` decisions. The existing live gates (ADR 0017 part F: employer compliance, the written stop criteria, the taxable cash account, a `safety-reviewer` pass on the live path) stay beside it; a passing `paper shakedown` does not by itself start Phase 6.

### `abandon`: the last resort, and the strictly-flat rule after it

**Pending T64b/T67** — `paper abandon`, the `abandoned` window state and this strictly-flat rule are scheduled as a dated amendment to [the spec](../specs/paper-trading.md) and are not yet built; the shape below is fixed by [T64b](../plans/paper-trading.md)'s plan line and is not yet confirmed CLI behavior.

`paper stop --reason` is the normal, code-driven way to close a window flat: it liquidates through the ordinary wrapper path and only ever carries forward listed residues (`dust`/`untradable`) matched both ways. Reach for `abandon` only when `stop` itself cannot complete — for example, unresolved mismatches or held positions that `reconcile_now` cannot explain and `stop`'s own checks therefore refuse. `abandon` does not place or cancel any order itself: it records a `paper_window_stops` row with `state = abandoned`, your `--reason` as the ADR-style note, and `residues_json` listing every position still held and every unresolved mismatch at that moment. It does not clean anything up.

```bash
uv run tradepartner paper abandon --reason "why stop could not close this window cleanly"   # (built by T64b/T67)
```

**After an `abandoned` window, the account must be strictly flat before the next `paper start` — no residue is carried forward, unlike a normal `closed` window.** The owner never trades by hand (spec req 14), so the one approved way to reach that strictly-flat state is **resetting the paper account at Alpaca directly** (its paper-account reset in the Alpaca dashboard), not placing manual offsetting trades. Do this before attempting the next `paper start`; it will otherwise refuse on a non-flat account. **Check whether the reset changes the account id or issues new paper keys.** `paper start` checks `account().account_id` and records it for the new window, and if Alpaca issued new `ALPACA_PAPER_API_KEY`/`ALPACA_PAPER_API_SECRET` values, update them in `.env` before running `paper start` again — only `.env`, never a command line, a commit, or this runbook.

## Sweeps and the quiet intervals

The strategy lab's `run_sweep` (T107; the `sweep run` command is T111) reads and writes the store, which `ingest` and `paper run` also use. So it keeps out of their way by **quiet intervals**: inside one it opens no trial, holds no connection and writes nothing. A group about to start whose predicted time would reach an interval waits; a group running when one begins pauses between steps with its connection closed and resumes after.

Two sources, both from `lab.*` and `paper.*` config:
- **`lab.quiet_intervals`** (default `[("16:00", "21:00")]`), local `HH:MM` pairs in `lab.quiet_timezone` (default `America/New_York`), on each day in `lab.quiet_weekdays` (default Monday to Friday, `[0, 1, 2, 3, 4]`). These must cover **every other launchd job's run**: the ingest plist (18:30 ET, plus the time it can run late or coalesce after a wake) and any job added later. Move them whenever the ingest plist's time moves.
- **The paper interval**, derived from `paper.submit_window_before_open_minutes`, `lab.paper_run_lead_minutes` (default 30), `paper.submit_window_after_open_minutes` and `paper.sell_wait_seconds`. With today's defaults it brackets the 08:05 ET paper plist; it moves with the `paper.*` keys, so nothing here needs editing when T70 sets them. On a weekday holiday it uses the regular 09:30 open.

`lab.quiet_timezone` must equal the machine's system time zone, the one launchd fires in. `lab status` (T111) warns when it does not.

A sweep only starts or resumes a group; it never blocks `ingest` or `paper run`, but a group that overruns into a plist's start is the failure this avoids, so check the intervals after any plist change. Every `run_sweep` applies the paper interval, whether or not a paper window is open.

## Placeholder: the `collect` job (after the collectors merge)

**Do not install this yet.** The [event-data spec](../specs/event-data.md) schedules `tradepartner collect` as a second, independent launchd job once its collectors merge. Until then the owner runs `collect` by hand. The task that merges the collectors replaces this section with the final plist. The shape is known now:
- **Label:** `com.tradepartner.collect`. Same `uv` path, `PATH`, `WorkingDirectory` and log directory as the ingest job, with logs at `collect.log` and `collect.err.log`.
- **Arguments:** `run --frozen tradepartner collect`. Every enabled source, forward only; backfills stay manual.
- **Schedule:** every `collect.interval_minutes`, every day, trading or not, as `StartInterval` in seconds rather than a calendar time.
- **No quiet interval.** It writes the events file (`events.path`) and its raw cache and lock files under `collect.raw_dir`, never the main store. It reads the main store once per run: one short, bounded, read-only read of the security master. It shares only the cross-process EDGAR pacing lock with `ingest`. It may overlap the 18:30 ingest, and a collector failure never blocks `ingest`. If ingest holds the main store for longer than `store.lock_retry_seconds`, that collect run exits non-zero and the next interval retries.
- **Sleep:** a `StartInterval` job also coalesces missed intervals into one run on wake. The collector keeps its place per source in `collection_state`, so a sleeping Mac loses nothing; it only delays.
