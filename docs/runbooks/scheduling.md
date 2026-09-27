# Runbook: scheduling the daily ingest

Owner-run. This page installs `tradepartner ingest` as a launchd job on the Mac that holds the real store. It also shows how to collect the evidence that closes Phase 2 plan T22: five consecutive scheduled `ok` runs ([data-foundation spec](../specs/data-foundation.md), exit criteria). Agents never run any of this. The store, `.env` and the job all live in the main checkout, which agents do not enter ([teams.md](../ways-of-working/teams.md)).

Later sections are added by later tasks. The `paper run` plist comes from T70b ([paper-trading plan](../plans/paper-trading.md)). The `collect` plist is a placeholder until the event collectors merge (last section).

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

## Placeholder: the `collect` job (after the collectors merge)

**Do not install this yet.** The [event-data spec](../specs/event-data.md) schedules `tradepartner collect` as a second, independent launchd job once its collectors merge. Until then the owner runs `collect` by hand. The task that merges the collectors replaces this section with the final plist. The shape is known now:
- **Label:** `com.tradepartner.collect`. Same `uv` path, `PATH`, `WorkingDirectory` and log directory as the ingest job, with logs at `collect.log` and `collect.err.log`.
- **Arguments:** `run --frozen tradepartner collect`. Every enabled source, forward only; backfills stay manual.
- **Schedule:** every `collect.interval_minutes`, every day, trading or not, as `StartInterval` in seconds rather than a calendar time.
- **No quiet interval.** It writes the events file (`events.path`) and its raw cache and lock files under `collect.raw_dir`, never the main store. It reads the main store once per run: one short, bounded, read-only read of the security master. It shares only the cross-process EDGAR pacing lock with `ingest`. It may overlap the 18:30 ingest, and a collector failure never blocks `ingest`. If ingest holds the main store for longer than `store.lock_retry_seconds`, that collect run exits non-zero and the next interval retries.
- **Sleep:** a `StartInterval` job also coalesces missed intervals into one run on wake. The collector keeps its place per source in `collection_state`, so a sleeping Mac loses nothing; it only delays.
