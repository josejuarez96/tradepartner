# Runbook: scheduling the daily ingest and paper run

Owner-run. This page installs `tradepartner ingest` and `tradepartner paper run` as launchd jobs on the Mac that holds the real store, and covers the owner's operating procedure for a scheduled `paper run`: what each alert means, the kill/resume procedure, and when `abandon` is the right call. Agents never run any of this. The store, `.env` and the jobs all live in the main checkout, which agents do not enter ([teams.md](../ways-of-working/teams.md)). It also shows how to collect the evidence that closes Phase 2 plan T22: five consecutive scheduled `ok` ingest runs ([data-foundation spec](../specs/data-foundation.md), exit criteria).

The `collect` plist is a placeholder until the event collectors merge (last section).

**Status of the `paper run` section below (2026-10-19).** The `paper` commands are built and in the CLI: `start`, `stop`, `run`, `reconcile`, `kill`, `resume`, `report`, `check`, `shakedown`, `status`, `abandon`, `override`, `settle` and `lots-reconcile`. Where this page still says **(built by T67)** or **(built by T64b/T67)** after a command, read it as a note on where the command came from; it does not mean the command is missing. The six `paper.*` timing keys were set from Probe 3's measurements (plan task T70), so the sample schedule below uses real values; if you change a key in `config.py`, recompute the schedule. Nothing below instructs bypassing a risk check, releasing the kill switch without a reconciliation, or putting a secret on a command line; where that is tempting there is no flag for it, by design (spec req 16).

## What the job does

Once a day, `tradepartner ingest` brings the store up to the **expected session**: the last XNYS session that had closed by `now - ingest.settle_delay_minutes` (60 by default). The trading calendar decides what a session is, so the job can run every day of the week:
- On a weekend or holiday it re-reads the last session and usually writes little beyond its run rows. EDGAR accepts filings on some market holidays, and late price revisions can land on any day.
- A run the next morning (after a missed evening, see "Sleep versus power-off") still targets the previous session.

Sources run in order, `edgar` then `alpaca`, and **every source runs even when an earlier one failed** (#2180): an EDGAR outage does not stop that evening's prices, and the run exits `1` if any source was not `ok`. Only a locked store ends the run early. Each source that runs writes one `ingestion_runs` row, with `mode = 'session'`, except when the store is locked: a locked source writes no row, and neither does a failed source whose row write is itself locked. So a failed evening can leave both rows, only an `edgar` row, or none. The command prints one line per source and exits with:
- `0`: every source is `ok`.
- `1`: a source was stale or failed, or the store was locked.
- `2`: a configuration error before any work, such as a missing `SEC_EDGAR_USER_AGENT` or Alpaca key. The error names the variable and never prints its value.

**When to run it.** The regular close is 16:00 ET, so the expected session is that day's from 17:00 ET on (13:00 closes are handled by the calendar). The plist below runs at **18:30 local time**, which leaves margin for late bars. If the Mac's time zone is not US Eastern, convert: for example, 18:30 ET is 15:30 PT. launchd uses the Mac's local clock and follows daylight saving time with it.

**Do not open the dashboard or any store reader during a job.** `ingest`, `paper run` and `paper kill` wait up to `store.writer_lock_retry_seconds` (300 s) for the store, longer than other commands (`store.lock_retry_seconds`, 60 s), so a render under five minutes is out-waited; a longer reader still locks them out (#2178). A Data health render with coverage holds the store for about 86 s. `paper kill` that cannot get the store says `kill switch NOT engaged: store busy (held by ...)` and exits 3: retry, or stop the reader. It prints `kill switch NOT engaged yet: store busy ... waiting up to 300 s` the moment it finds the store locked, and waits once per book. The paper run re-checks the submit window before every order, so a long wait never sends an order after the window closes (the order is closed `not_received` and re-attempted by the next in-window run).

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

`tradepartner ingest` and paper write commands `start`, `stop`, `run`, `reconcile`, `resume`, `abandon`, `settle` and non-kill-switch `override` migrate an older store before journaling or calling the broker. **`paper kill` and `paper override --kind engage_kill_switch` skip migration so a migration failure cannot block engagement.** Paper read-only commands (`report`, `check`, `shakedown`, `status` and `lots-reconcile`) refuse an older store with the stored and code schema versions and tell you to run ingest or a paper write command that migrates. There is no separate `migrate` command or prompt. The first scheduled `ingest` or `paper run` after a schema bump migrates the store; older code cannot read it afterwards. The code's version is `CURRENT_SCHEMA_VERSION` in `src/tradepartner/store/schema.py`. Check before you pull:

```bash
git fetch origin && git diff HEAD origin/main -- src/tradepartner/store/schema.py | grep '^[-+]CURRENT_SCHEMA_VERSION ='
```

Any output means the pull migrates the store on its next ingest or migrating paper write command. If you have already pulled a bump and are not sure one has run since, take the copy anyway: an extra one costs nothing.

**The full routine is its own page: [update-the-system](update-the-system.md).** It has the exact commands to unload every job and confirm it is idle, copy the store (the file, its `.wal`, and the commit you were on), pull, `uv sync`, load the jobs again, and check with `paper status --all` and `health --check`. Its Part 2 is the restore, for a bad update and for a store damaged by a disk fault or power loss.

Two rules stay true here. Unloaded jobs never run, so load them again right after the pull; the next scheduled run then migrates the store. And after a restore, if a window traded between the copy and the restore, expect a `reconciliation` alert on the next run until you have sorted out what the broker did in the gap: the run halts and the switch engages rather than silently losing track of an order.

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

**Two jobs, one Mac: keep it awake on power (#1759).** The ingest runs at 18:30 and the paper run at 08:05 the next morning. macOS keeps **one** repeating wake event, so a wake cannot cover both: a wake at 18:28 leaves the Mac asleep at 08:05 (the paper run slips outside its submit window), and a wake at 08:00 moves that evening's ingest to the morning, where it runs beside the paper job and the run that waits for the store lock may end `locked` (and a coalesced ingest that finishes after 08:05 halts that day's paper run with `stale_data`). So do not use a wake for this. Stop the Mac sleeping while it is on power, and remove any old wake. Run these yourself in Terminal; they need admin rights:

```bash
sudo pmset -c sleep 0          # never sleep on the charger (the display may still sleep)
sudo pmset repeat cancel       # clears all repeating wake, power-on, sleep and shutdown events
pmset -g custom                # confirm: under "AC Power", sleep is 0
pmset -g sched                 # confirm: no repeating event listed
```

The same setting is in System Settings: Battery (a laptop) or Energy (a desktop), Options, "Prevent automatic sleeping on power adapter when the display is off". `caffeinate -i` in each plist still holds the Mac awake for the run itself, and costs nothing when the Mac is already awake.

- **The Mac must stay on power and logged in.** Powered off, the run is skipped (table above). A laptop on battery with the lid closed may not wake fully for a scheduled event: leave the lid open on power, or use it in clamshell mode with a display and power attached.
- **A restart needs your login.** After a restart the Mac stops at the login screen (or the FileVault unlock) and no LaunchAgent runs until you log in. Log in after any restart, then check the next morning's `paper status --all`.
- **If you would rather let the Mac sleep,** you accept that one of the two jobs runs late: pick `sudo pmset repeat wake MTWRFSU 18:28:00` and the paper run does not happen on time, or a wake at 08:00 and the ingest does not. This runbook does not recommend either.
- **Check the first week's log.** A run that started without network, because Wi-Fi had not reconnected yet, fails its sources and shows it there. If the Mac was restarted, that is the likely cause.

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

**Wake coverage for two jobs.** One wake cannot cover both the 18:30 ingest and the 08:05 paper run, because macOS keeps a single repeating wake (see "Sleep versus power-off" above, which has the commands). Keep the Mac on power with sleep off (`sudo pmset -c sleep 0`) and remove any old wake (`sudo pmset repeat cancel`). `caffeinate -i` in each plist is then enough to survive the run itself.

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

**`--book` on every command.** Every `paper` command except `shakedown` takes `--book <token>`, default `paper.book_id` (`main`): `start`, `stop`, `run`, `reconcile`, `kill`, `resume`, `report`, `check`, `status`, `abandon`, `override`, `settle` and `lots-reconcile` (which reads that book's lot set). A token outside the grammar is a usage error (exit 2) before the store is opened. `--book` picks a book's paper key pair, never an endpoint. Examples:

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

Every alert is a row in the `alerts` table first (the source of truth), delivered also to `macos` and `email` if `alerts.channels` and, for email, the four `ALERT_*` variables are set — except `kill_switch_write_failed` (see its row below), which never reaches the store and goes out only through the other channels. `paper status` and the operations page list stored alerts. The table lists every kind the code can send (`ALERT_KINDS` in `execution/alerts.py`, 19 kinds). `alerts.channels` must include at least one of `macos`/`email` (enforced at config load, #366 Q22 (iii)), precisely so a `kill_switch_write_failed` alert always reaches you even though it never touches the store.

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
| `no_window` | `paper run` ran with no open window; `kill`, `resume`, `reconcile`, `abandon` and `override` are refused the same way but write no alert of their own. | Normal before the first `paper start` or after `paper stop`. If you expected a window to be open, check `paper status`. Once the final window has closed for good, `launchctl bootout` the paper job so it stops alerting daily, and pause the paper heartbeat check (a no-window run pings `/fail`; a booted-out job mails as missed). A failure before the run starts (bad settings) sends no ping; the check then fires at its grace. |
| `lot_ledger` | The run's lot ledger (the per-lot cost record behind realised P&L) could not be rebuilt. The alert message gives the error. The run itself still finishes; a note goes on its result row. | Read the message. The switch is not engaged and no resume is needed. A realised P&L the run held back is written by the next run due to write it, so the figure arrives late, not wrong. If it repeats on every run, raise it as an issue with the message (`execution/run.py`, `_outcomes`). |
| `stale_mark` | A tradable name you hold had no bar for S−1, so the run marked it at its last close (one alert per run, naming each name and the gap in sessions). | Usually a late or missing bar: check the ingest job and re-ingest. No switch change. This is allowed for at most `paper.max_stale_mark_sessions` consecutive sessions (default 3, frozen when the window opens; 0 fails at once). Past that the run fails, so fix the data before the gap reaches the limit. |
| `missing_candidate_bar` | A name the strategy wanted to consider had no bar for the rebalance session (T_0), so it was left out of the plan. The run goes on and trades the rest. If you already hold it, it is kept at its current quantity until the next rebalance. | Check the named names in the message. Re-ingest the missing bar if it should exist, or accept that the next rebalance decides again. This is not `stale_data`: it never halts the run. |
| `skipped` | One or more names were skipped one by one: the broker refused the buy (not on the asset list, not fractionable, or not tradable), or the name was skipped before submission in the run's own phases. The message names each name and its reason (`skip_asset_unknown`, `skip_not_fractionable`, `skip_untradable`). One alert per run. | The rest of the run trades normally and the switch stays off. Check whether the name should be in the universe. Too many skips in one rebalance trips `skip_cap` (above). |
| `pending_unresolved` | The run's first acknowledgement timeout, on any order (buy or sell): the order was sent but the broker had not acknowledged it within `paper.accept_wait_max_seconds` (default 300). The message names its `client_order_id`. | The run goes on with the next order. The next run settles this one by reading it back from the broker (the read-back is in the next run's notes). A second unacknowledged order in the same run raises an acknowledgement-timeout fault and halts: handle that as `halted`. Check the broker status page if it repeats. |

**A delisted holding whose cash is late (#411).** When the broker removes a delisted name with no cash, `paper run` keeps trading and accepts a credit tagged to that symbol (an `MA`, `REORG` or `VOF` activity, at most last close x quantity x (1 + `risk.delisting_cash_max_premium`, default 0.10)) for `risk.reconcile_merger_lag_sessions` sessions (default 5), booking it as the sale proceeds. If none arrives, nothing halts and no alert is sent: `paper status` (and `--all`) and the Operations page show `<symbol>: delisting cash overdue since <session> — check Alpaca's activity` until cash is booked for it; look at the broker's activity for that symbol. A credit after the window halts on `cash`, and so does one the window's last reconciliation only deferred (a lagging order) if the window closes before the next `ok` one.

### A halt, off the Mac: the heartbeat and the halt card (#2155)

The Mac cannot tell you it is off the network: on 2026-10-21..25 both books sat halted for three days with no one told. So each job also pings an outside **dead-man's-switch** check when it ends (`heartbeat.py`): the plain URL on success, `<url>/fail` on a non-zero outcome (a halt, a crash, `no_window`, stale ingest). The paper check also pings `/fail` while any open-window book's kill switch is engaged, even though a `skipped_kill_switch` or weekend `no_session` run exits 0: a halted book keeps the check red until `paper resume` (#2179). A manual `paper run --book X` only ever pings `/fail`, never OK; only the scheduled every-book run turns the check green. The service mails you when a ping fails **or does not arrive** by its schedule plus grace, which also covers a Mac that is off, asleep or offline. The ping has a short timeout (`alerts.heartbeat_timeout_seconds`, default 5), never changes the job's exit code, and logs only the job name and error type, never the URL. No URL in `.env`, no ping.

**One-time setup (yours; agents never touch `.env`).**
1. On healthchecks.io (or a similar service) create two checks. `paper run`: schedule `5 8 * * *` (daily, like the plist) in the Mac's timezone, grace 60 minutes. `ingest`: `30 18 * * *`, grace 60 minutes. Point each check's email integration at an address your phone alerts on.
2. Put each check's ping URL in `.env`: `ALERT_HEARTBEAT_URL_PAPER=...` and `ALERT_HEARTBEAT_URL_INGEST=...`. Each URL is a secret, scrubbed from output like the keys; never paste it into a plist, chat or issue. Every day pings, weekends and holidays included (`no_session` exits 0), so a Friday-night outage is caught on Saturday morning, and a book halted on Friday keeps the check red all weekend.
3. Add `email` to `alerts.channels` and set the four `ALERT_SMTP_*` / `ALERT_EMAIL_TO` variables (`.env.example`), so the Mac's own alert mail also reaches your phone while the network is up. The heartbeat covers the case where it is not.
4. Check once: `curl -fsS "$URL"` for each URL from the Mac turns the check green; `"$URL/fail"` should mail you.

**The halt card.** A `halted` alert names a fault kind. For each: what it means, what to do, and the line to run once the cause is fixed. The `paper resume` line is per book and always reconciles first; run `paper status --all` before and after.

| Fault in the alert | What it means | Fix path |
|---|---|---|
| `ClockError` | The clock returned an implausible or non-monotonic time. | Check the Mac's date and time sync, then resume. |
| `ReconciliationError` | The ledger and the broker disagree and nothing explains it. | Read the mismatch in `paper status`; look at the broker's own account page. Resolve it (an order the broker filled or cancelled), then resume; resume refuses while it still fails. |
| `RejectionCapError` | Too many of the run's orders were rejected by the broker. | Find the cause (symbol halted, account restriction), fix it, then resume, adding `--accept-rejections` only per the rule below. |
| `SkipCapError` | Too many per-name skips in one rebalance. | Read the skip reasons in the alert; fix the data or universe cause, then resume. |
| `LimitBreachError` | A planned or resulting position breaks a `risk.*` limit. | Read which limit; do not raise it to get past the halt. Resume once the plan is back inside. |
| `AcknowledgementTimeoutError` | A submitted order was not acknowledged within `paper.accept_wait_seconds`. | Check the broker's status page and the order on its site; resume settles the pending order from the broker. |
| `UniverseCollapseError` | The plan's universe fell under `paper.min_universe_names` or dropped too far. | Check the universe data and ingest (`health --check`); resume after the data is right. |
| Anything else (a malformed broker row, a bad `.env` value, no network) | `run_failed`/`halted` with the exception named in `paper.err.log`. | Fix the cause from the log (network back, credentials, a data row), then resume. |

```bash
uv run tradepartner paper status --all
uv run tradepartner paper resume --book <book> --reason "root cause and what you checked"
```

A halt does not close positions: while a book stays halted its positions are held as they were. If you would rather flatten, that is `paper abandon`, below; it is the last resort.

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

**`account_mismatch`: the broker's account is not the window's.** `paper resume` and `paper abandon` first read the broker's `account_id` and compare it with the one the window recorded at `paper start`; `paper reconcile` does the same and engages the switch with an `account_id:` reason. On a mismatch they refuse before writing anything broker-derived: another account's broker never saw this window's orders, so settling them against it would journal live orders as `not_received` and, for `abandon`, record that account's positions as the window's. The usual cause is the wrong key pair in this book's variables (another book's pair pasted in). The way back is to put the right keys back for that book, then run `paper resume --reason "..."` again; the journal is untouched, so nothing needs repairing.

**When `--accept-broker-fills` is the right call.** Normally omit it. Add it only when:
- An order has been `fills_lagging` for `risk.max_fill_lag_sessions` or more sessions (the spec's bound) — the journal's fill total is behind what the broker's `get_order` reports, and the fill-stream has never caught up on its own, **and**
- You have checked `paper status`/the reconciliation view and confirmed the broker reports that specific order as **finished** (not still open) — an order the broker still holds open is refused even with the flag, since it may yet fill through the normal path.

```bash
uv run tradepartner paper resume --accept-broker-fills --reason "order <id>: broker reports filled, feed never delivered it after N sessions"   # (built by T61b/T67)
```

With the flag, for each qualifying order `paper resume` journals a synthetic fill for the residual quantity only (broker's `filled_quantity` minus what's already journaled), priced from the implied residual and flagged `price_implied`. **The synthetic fill is the one that stands**: if a real fill for that order arrives later anyway, it is journaled `superseded_by` the synthetic one and every reader filters it out — the implied price is not retroactively corrected to the real one. Without the flag, resume refuses outright while any such order exists; this is the owner's considered decision to trust the broker's number over a missing feed message, not something to reach for reflexively because resume is otherwise refusing.

**When `--accept-rejections` is the right call.** Normally omit it. Before it releases, `paper resume` judges the rejections on every run the release would clear (each `halted`, `crashed` or `failed` run of the window that no earlier release cleared, #397/#451) and refuses while any of them breached the rejection cap (all of a run's orders rejected, or more than the frozen `risk.max_rejections_per_run`). The refusal names each run's verdict. Add the flag only when:
- resume refused with such a verdict on a halted, crashed or failed run, **and**
- for **every** run the refusal names, you have found why the broker rejected its orders (symbol halted, account restriction, bad request shape; see `rejection_cap` above), fixed it or confirmed it will not recur, and accept its rejections as they stand. The flag is all or nothing: it accepts the verdicts on all the runs this release would clear at once, never one run at a time.

```bash
uv run tradepartner paper resume --accept-rejections --reason "run <id>: rejected because <cause>; fixed/checked <what>"
```

The flag accepts **only those rejection-cap verdicts**, on the runs this release would clear. Resume journals a `resume_acceptances` row naming each accepted verdict (the CLI prints one `accepted ...` line each) before any reconciliation or release, and then every other step still applies: a rejection-cap verdict from this resume's own collection on any other run, the `fills_lagging` bound (that is `--accept-broker-fills`), reconciliation and every release check still refuse. It lifts no other refusal and bypasses no limit; the two flags are independent and may be given together when both apply. The acceptance does not carry over: if a flagged resume still refuses for another reason, the next `paper resume` needs `--accept-rejections` again.

### Corporate-event halts before the automatic rules land (#2183, #2184, #2185)

Three corporate events can stop a book today. On 2026-10-26 the owner decided the rules that will explain them automatically; they are built after the shakedown span (a plan task each), so until then you meet them as halts. Check the dates first: `paper status --book <token>` and the Reconciliation card show the mismatch, and Alpaca shows what really happened (its dashboard Activities page; its exact name is NOT VERIFIED from the repo).

**There is no command that books the cash or the symbol for you.** `paper resume` reconciles first and refuses while the credit is unexplained, with no flag that skips it (above). `paper override` takes only `exclude_name`, `keep_name` and `engage_kill_switch`; none explains a broker credit. The base moves only on an `ok` reconciliation, so a rerun of `paper reconcile --book <token>` shows the same difference. The one existing way out of a cash or quantity halt is to end the window: `paper abandon`, reset the Alpaca paper account, then `paper start` ([abandon](#abandon-the-last-resort-and-the-strictly-flat-rule-after-it), [window-restart](window-restart.md)). **The two options cost the span evenly.** The halt has already restarted any open shakedown span ([shakedown](shakedown.md), "Mismatch or halt"), and leaving the book halted writes no `released` row, so the span cannot pass either. `abandon` adds to that: it ends the window's paper record and, through the Alpaca reset, the positions and possibly the account's history. Either is the owner's decision; tell the orchestrator first.

| Event | How you recognise it | Verify at Alpaca | What to do now |
|---|---|---|---|
| Dividend credited on its pay date, weeks after the ex-date (#2183) | `reconciliation` alert, kill switch engaged. Mismatch kind `cash`, detail `broker <cash>, ledger <cash>`, the broker higher by shares held at ex-date times the amount per share (a `DIV` activity), on a book that held the name over its ex-date. | Activities for the book's account: one `DIV` row for that symbol, dated the pay date, amount equal to the cash gap. A gap with no matching `DIV` row is not this event: stop and ask. | No lighter command exists. If the cause is only this credit, the options are the `abandon` path above, or leave the book halted and tell the orchestrator, who decides with you before the rule lands. Never place or cancel anything in Alpaca's dashboard. |
| Split with a fractional result, cash in lieu (#2184): a split that leaves a fractional share, possible on any reverse split or a forward split above 1.25 | `reconciliation` alert. Mismatches `quantity` (broker's whole shares against the ledger's) and `cash`, usually with `unexplained_activity`, the detail naming a `CIL` activity. | Activities (page name NOT VERIFIED): a `CIL` row for that symbol, about the fractional remainder times the price, and the position showing the whole-share result. | Same as above: no command books the `CIL`; `abandon` path or wait for the owner's decision. |
| Ticker change (#2185) | A **halt**. A sell, trim or forced exit of the renamed held name gets a 404 from Alpaca for its old ticker, and the run halts with a `reconciliation` alert, the kill switch engaged, and the message `the broker does not know held names [<security_id>]` (`wrapper.py`, `_refuse_unknown_held`). A buy that adds to the held name halts the same way. `paper reconcile` meanwhile reads `ok` and maps the new symbol. | The old and the new ticker's asset pages, and the position's symbol in Alpaca's Positions (dashboard page names NOT VERIFIED from the repo). Look hardest at whether the old ticker now belongs to a different company. | `paper resume` will reconcile `ok` and release, and the next run that sells the name halts again; no command repairs the symbol. Keep the book halted and tell the orchestrator, or take the `abandon` path. If the old ticker is reused by another issuer, a sell could reach the wrong security: keep the switch engaged and do not resume. |

The rules, when built: a dividend is expected from the ex-date (shares held times amount) and accepted when the credit tagged to the symbol falls between the ex-date and the pay date plus a configured grace, within a configured tolerance; a split's cash in lieu is accepted within a configured number of sessions and tolerance of the fractional remainder times the price; reconcile and the order path share one symbol map, and a name whose symbol is ambiguous that session is skipped, never traded under the old symbol. Unmatched or unexpected cash still halts in every case.

### The kill-switch drill (ADR 0017 part E.4)

The drill proves, on the real store, that an engaged switch stops a scheduled run from submitting anything. It is one of the seven shakedown lines (E.4) and is run **once, on book `daily`, on one daily-book session** inside the shakedown span: **2026-10-27** (owner decision 2026-10-20, #2086). `daily` hosts it because it is a fast book that is not a forward exam: the drill costs it at most one rebalance (delayed to a catch-up run, or `missed` with reason `kill_switch` if the catch-up lapses), reported by `paper report` and never an exam result. The owner times it.

1. **Before the scheduled run, on the 2026-10-27 session** (after the previous session's ingest, before the job's start, 08:05 ET with today's plist), engage the switch yourself:
   ```bash
   uv run tradepartner paper kill --book daily --reason "drill"
   ```
   The `--reason` is journaled; the `kill_switch` row it writes has `state = engaged` and `source = owner`, which is what E.4 reads. Confirm with `uv run tradepartner paper status --book daily` that the switch shows engaged. Engage it **only** for `daily`: `--all` would drill every book and cost each its rebalance.
2. **Let the scheduled run skip.** Do not start the run yourself and do not `kickstart` it: the line needs a run with `invoked_by = 'scheduler'` (and a kickstart inside the window can trade if the switch is not engaged). The job still collects, reconciles and marks, and submits nothing; `daily`'s result is `skipped_kill_switch` (exit 0) with **no `orders` row**. Check it in `paper status --book daily` after the run. Any other book with an open window runs normally in the same job.
3. **Resume the same day**, once the run has finished (never before it, or the run trades):
   ```bash
   uv run tradepartner paper resume --book daily --reason "drill complete: <what you checked>"
   ```
   Resume settles, collects, reconciles and appends the `released` row, exactly as in "Kill switch" above; if reconciliation fails it refuses and the switch stays engaged, so find out why before trying again. The `released` row is the third thing E.4 reads. The drilled rebalance trades on a later catch-up run if the catch-up window allows it, else it is logged `missed` (reason `kill_switch`).

Nothing in the drill bypasses a risk check or releases the switch without a reconciliation. If the drilled session shows an `orders` row for `daily`, or the run's result is anything but `skipped_kill_switch`, stop and treat it as an incident: that is a kill-switch defect, not a drill result.

### The shakedown: the span, notes and `paper shakedown`

The Phase 4 exit is [ADR 0017](../decisions/0017-fast-paper-and-machine-readiness-gate.md) part E: `tradepartner paper shakedown` exits 0 on the real store with all seven lines passed over every book, and the Phase 4 retro records the output.

**Day by day:** the owner's checklist for the T162 span (2026-10-22 to 11-04) is [shakedown.md](shakedown.md).

**Open the span** with one owner decision:

```bash
uv run tradepartner decision shakedown-span --sessions 10 --order-sessions 5 --reason "..."
```

`--sessions N` is how many sessions the span needs and `--order-sessions M` how many of them need a live fill (decided: N = 10, M = 5; a longer span keeps one order session per two sessions). The `--reason` states why the span opens now; it names no live strategy (*amended 2026-10-18, ADR 0023 D6*: the live book is chosen in T163, after the shakedown and the first kill-ladder verdicts; this supersedes ADR 0017 open question 8). The span is every XNYS session from the session after the row's time through the last completed session. The thresholds are read from that row, never from `config.py` or `.env`, so the bar cannot move after a failing span. A new `shakedown-span` row restarts the span, and the rerun is recorded. Open it so that an H1 fill session lies inside the ten (the drill needs one).

**A `mismatch` reconciliation or a real halt inside the span restarts it**: the span then starts at the session after the `released` kill-switch row that followed it. Fix the cause and resume; open a new `shakedown-span` row only if you want the count to start later.

**Annotate an alert** that the span would otherwise fail on, by the alert's id (`paper status` does not print it, #2095; [shakedown.md](shakedown.md) shows a read-only query) (a `stale_data` or `missed_run` alert):

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

`paper stop --reason` is the normal, code-driven way to close a window flat: it liquidates through the ordinary wrapper path and only ever carries forward listed residues (`dust`/`untradable`) matched both ways. Reach for `abandon` only when `stop` itself cannot complete — for example, unresolved mismatches or held positions that `reconcile_now` cannot explain and `stop`'s own checks therefore refuse. `abandon` does not place or cancel any order itself: it records a `paper_window_stops` row with `state = abandoned`, your `--reason` as the ADR-style note, and `residues_json` listing every position still held and every unresolved mismatch at that moment. It does not clean anything up.

```bash
uv run tradepartner paper abandon --reason "why stop could not close this window cleanly"   # add --book <token> for a book other than main
```

**After an `abandoned` window, the account must be strictly flat before the next `paper start` — no residue is carried forward, unlike a normal `closed` window.** The owner never trades by hand (spec req 14), so the one approved way to reach that strictly-flat state is **resetting the paper account at Alpaca directly** (its paper-account reset in the Alpaca dashboard), not placing manual offsetting trades. Do this before attempting the next `paper start`; it will otherwise refuse on a non-flat account. **Check whether the reset changes the account id or issues new paper keys.** `paper start` checks `account().account_id` and records it for the new window, and if Alpaca issued new `ALPACA_PAPER_API_KEY`/`ALPACA_PAPER_API_SECRET` values, update them in `.env` before running `paper start` again — only `.env`, never a command line, a commit, or this runbook.

Closing a window and opening a new one is a restart, and it resets the paper record: see [window-restart](window-restart.md). New or changed Alpaca keys: [key-rotation](key-rotation.md).

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
