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

## Scheduling `paper run`

Install this as part of [T71](../plans/paper-trading.md), right after `paper start` succeeds on the real store — T71's own evidence list includes "the plist installed." Before that, there is no open window for the job to act on anyway: a `paper run` with no window open just writes a `no_window` alert and exits (see the alert table below), so there is nothing to gain by installing or running it earlier.

### What the job does

Once per XNYS session, `tradepartner paper run` **(built by T67)** does one of two things, depending on whether S (the session the run targets) is a fill session:
- **Every session:** collects order outcomes since the last run, reconciles the ledger against the broker, and marks the account (drawdown check). While the kill switch is engaged it still does this much, read-only against the broker, but submits nothing.
- **On a fill session, if the run starts inside the submit window:** also plans, submits sells, waits for them to finish, then submits buys sized from the cash the sells raised. A fill-session run that starts outside the window leaves the rebalance `pending` for a later in-window run; it submits nothing, forced exits included.

**The submit window** is `[open(S) − paper.submit_window_before_open_minutes, open(S) + paper.submit_window_after_open_minutes]`. With the current config defaults (`submit_window_before_open_minutes=90`, `submit_window_after_open_minutes=30`, `sell_wait_seconds=900`, `poll_interval_seconds=15`, `accept_wait_seconds=30`) the window opens 90 minutes before the open and closes 30 minutes after it — **pending T70 / Probe 3**: these are the spec's placeholders, not measured values, and the window's edges will move once T70 sets the six timing keys from Probe 3's report. Recompute the schedule below against whatever `paper.submit_window_before_open_minutes` is on main at the time you install the job (`uv run python -c "from tradepartner.config import get_settings; print(get_settings().paper)"`).

The job must start **after the previous session's `ingest` has completed** (the scheduler assumption in the spec's "Users & usage") and **inside the submit window**, so schedule it a few minutes after the window opens: that gives margin for `uv sync` and the clock pre-check without eating into the window reserved for sells-then-buys. With today's placeholder defaults (open at 9:30 ET, window opens at 8:00 ET) that means roughly **08:05 ET**; the 18:30 ET ingest job from the previous evening is long finished by then, unless that evening's run itself ran late or coalesced on a wake (see "Sleep versus power-off" above) — a coalesced ingest finishing after 08:05 pushes `stale_data` into that day's paper run instead of a halt, and no kill switch engages for it.

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

`TRADEPARTNER_INVOKED_BY=scheduler` combined with a non-interactive `stdin` makes the run's `invoked_by` field `scheduler`; only a `scheduler`-invoked, successfully executed rebalance counts toward `paper.min_rebalances` for the phase exit check (`paper check`). **A `launchctl kickstart` run is also `invoked_by=scheduler`, not `tty`**: kickstart starts the job under launchd with the plist's own `EnvironmentVariables` and a non-TTY stdin, exactly like the scheduled run, so it counts toward the exit-criteria evidence the same way. Only a run you start yourself from an interactive Terminal (no `TRADEPARTNER_INVOKED_BY`, a real TTY) is `invoked_by=tty` and does not count — the opposite of the ingest evidence's scheduled-vs-manual split, where `kickstart` does *not* count. **Never `kickstart` the paper job on a fill session or inside the submit window** just to test it; it will submit real paper orders and that test rebalance counts toward the exit criteria. Use `uv run tradepartner paper status` to check state, or a manual Terminal `tradepartner paper run` outside the window, instead.

Install, test and pause it exactly as for ingest ("Install, test, remove" above), substituting `com.tradepartner.paper` and `paper.log`/`paper.err.log`:

```bash
mkdir -p ~/Library/Logs/tradepartner
plutil -lint ~/Library/LaunchAgents/com.tradepartner.paper.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tradepartner.paper.plist

launchctl kickstart gui/$(id -u)/com.tradepartner.paper
launchctl print gui/$(id -u)/com.tradepartner.paper | grep -E "state|last exit code|runs"
tail -n 40 ~/Library/Logs/tradepartner/paper.log ~/Library/Logs/tradepartner/paper.err.log
```

As above, a `kickstart` run **counts as `scheduler`-invoked**, the same as a real scheduled run, and can submit real paper orders if a fill session is in progress and the submit window is open. Prefer `uv run tradepartner paper status` **(built by T67)** to check state without placing anything; reserve `kickstart` for a session where you have confirmed, via `paper status`, that it is not a fill session, or deliberately want a scheduler-attributed run.

### Checking a run

After a scheduled run, before moving on with your day:

```bash
uv run tradepartner paper status        # (built by T67) prints the operations-page numbers
```

This shows: the store's "as of" and the run's "last updated" (with a stale chip if S−1 has no run row), the four KPIs including the kill-switch chip, today's plan ranking, fills, the per-order journal chain, alerts, and reconciliation status. Read this, or the operations page, most days — it is the one thing the spec asks the owner to do routinely.

### What each alert kind means, and what you do

Every alert is a row in the `alerts` table first (the source of truth), delivered also to `macos` and `email` if `alerts.channels` and, for email, the four `ALERT_*` variables are set — except `kill_switch_write_failed` (see its row below), which never reaches the store and goes out only through the other channels. `paper status` and the operations page list stored alerts. Consider enabling `macos` in `alerts.channels` so a `kill_switch_write_failed` alert still reaches you.

| Alert kind | What happened | What you do |
|---|---|---|
| `halted` | A system fault (bad data, broker error, clock fault, …) stopped the run partway and engaged the kill switch. | Read the alert message and `paper.err.log` for the underlying exception. Fix the cause (network, broker outage, a bad `.env` value), then follow the resume procedure below. |
| `run_failed` | Any other exception the run didn't classify as a specific fault; the run's result is `failed`. | Same as `halted`: read `paper.err.log`, fix the cause, then resume. A crash (power loss, process killed) leaves no result row at all and engages the switch the same way through the derived state (see Kill switch below), with no alert of its own — `missed_run` is what you'll see for that session instead. |
| `stale_data` | Ingest hasn't reached S−1 for the reference symbol yet. | Check the ingest job ran and was `ok`. This does **not** engage the kill switch — once ingest catches up, the next `paper run` proceeds on its own; no resume needed. |
| `kill_switch` | The switch's engaged/released state changed (a halt, a drawdown trip, your `kill`, or a release). | Confirm which: `paper status` shows current state and the triggering source. |
| `kill_switch_write_failed` | The kill-switch row itself couldn't be written — the store may be unreliable. | Delivered outside the store (macOS/email only, since the store can't be trusted). Stop and check the store/disk before anything else; do not resume until you've confirmed the store is healthy. |
| `reconciliation` | The ledger and the broker disagree on a mismatch `reconcile_now` couldn't explain. | Read the mismatch in `paper status`. This halts and engages the switch; see Kill/resume below. |
| `rejection_cap` | Too many of this run's orders were rejected by the broker (or all of them were). | Check why the broker is rejecting (symbol trading halted, account restriction, bad request shape). The switch is engaged; investigate before resuming. A later run's collection can also surface this for an earlier run's orders. |
| `skip_cap` | Too many per-name skips in one rebalance (other than `dust`/`untradable`). | `SkipCapError` is a system fault like any other: it halts the run and **engages the kill switch**. Read the skip reasons in the plan/decisions view, follow the resume procedure below, then the rebalance stays `missed` reason `skip_cap` — it is not retried until the next rebalance event. |
| `missed_run` | No `paper_runs` row exists for S−1 — a scheduled run never happened. | Check whether the Mac was off (not asleep) at the scheduled time, or the job was disabled. See "Sleep versus power-off" above and "Wake coverage for two jobs" below; both apply to this job too. |
| `missed_rebalance` | A rebalance event was logged `missed`, reason one of `catch_up_lapsed`, `kill_switch`, `limit_breach`, `skip_cap` or `window_stop`. | Read the reason in the alert message; it names which of the above stopped this rebalance from trading. |
| `drawdown` | Ledger equity fell more than `risk.max_drawdown` below the window's peak. | This engages the kill switch automatically (a risk rule, not a judgment call). `paper stop` is refused while the switch is engaged, so you cannot go straight to stopping: `paper resume --reason "..."` first (which also resets the drawdown peak to the ledger equity at that point — a further drop from the *new*, lower peak can trip it again), and only then decide whether to let trading continue or `paper stop --reason "..."` right away. |
| `unspent_cash` | After buys executed, leftover cash exceeded `risk.max_unspent_cash_fraction` of equity. | Informational; check the plan's sizing on `paper status`. Does not engage the switch. |
| `locked` | A second instance found the run lock (`<store.path>.paper.lock`) held — by another `paper run`, or by a `resume`, `reconcile` or `stop` in progress. Ingest uses a separate store lock and is not a cause of this one. | No `paper_runs` row was written for this attempt; the lock holder's own run proceeds normally. If it recurs, check for a stuck process holding the paper lock. Avoid running `resume`/`reconcile`/`stop` right at the scheduled start time. |
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

### `abandon`: the last resort, and the strictly-flat rule after it

**Pending T64b/T67** — `paper abandon`, the `abandoned` window state and this strictly-flat rule are scheduled as a dated amendment to [the spec](../specs/paper-trading.md) and are not yet built; the shape below is fixed by [T64b](../plans/paper-trading.md)'s plan line and is not yet confirmed CLI behavior.

`paper stop --reason` is the normal, code-driven way to close a window flat: it liquidates through the ordinary wrapper path and only ever carries forward listed residues (`dust`/`untradable`) matched both ways. Reach for `abandon` only when `stop` itself cannot complete — for example, unresolved mismatches or held positions that `reconcile_now` cannot explain and `stop`'s own checks therefore refuse. `abandon` does not place or cancel any order itself: it records a `paper_window_stops` row with `state = abandoned`, your `--reason` as the ADR-style note, and `residues_json` listing every position still held and every unresolved mismatch at that moment. It does not clean anything up.

```bash
uv run tradepartner paper abandon --reason "why stop could not close this window cleanly"   # (built by T64b/T67)
```

**After an `abandoned` window, the account must be strictly flat before the next `paper start` — no residue is carried forward, unlike a normal `closed` window.** The owner never trades by hand (spec req 14), so the one approved way to reach that strictly-flat state is **resetting the paper account at Alpaca directly** (its paper-account reset in the Alpaca dashboard), not placing manual offsetting trades. Do this before attempting the next `paper start`; it will otherwise refuse on a non-flat account. **Check whether the reset changes the account id or issues new paper keys.** `paper start` checks `account().account_id` and records it for the new window, and if Alpaca issued new `ALPACA_PAPER_API_KEY`/`ALPACA_PAPER_API_SECRET` values, update `.env` (key names only, never the values anywhere else) before running `paper start` again.

## Placeholder: the `collect` job (after the collectors merge)

**Do not install this yet.** The [event-data spec](../specs/event-data.md) schedules `tradepartner collect` as a second, independent launchd job once its collectors merge. Until then the owner runs `collect` by hand. The task that merges the collectors replaces this section with the final plist. The shape is known now:
- **Label:** `com.tradepartner.collect`. Same `uv` path, `PATH`, `WorkingDirectory` and log directory as the ingest job, with logs at `collect.log` and `collect.err.log`.
- **Arguments:** `run --frozen tradepartner collect`. Every enabled source, forward only; backfills stay manual.
- **Schedule:** every `collect.interval_minutes`, every day, trading or not, as `StartInterval` in seconds rather than a calendar time.
- **No quiet interval.** It writes the events file (`events.path`) and its raw cache and lock files under `collect.raw_dir`, never the main store. It reads the main store once per run: one short, bounded, read-only read of the security master. It shares only the cross-process EDGAR pacing lock with `ingest`. It may overlap the 18:30 ingest, and a collector failure never blocks `ingest`. If ingest holds the main store for longer than `store.lock_retry_seconds`, that collect run exits non-zero and the next interval retries.
- **Sleep:** a `StartInterval` job also coalesces missed intervals into one run on wake. The collector keeps its place per source in `collection_state`, so a sleeping Mac loses nothing; it only delays.
