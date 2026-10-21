# Runbook: the T162 shakedown span, day by day

What you do on each day of the span, 2026-10-22 to 2026-11-04. The rules behind it are in [scheduling.md](scheduling.md#the-shakedown-the-span-notes-and-paper-shakedown) (what the seven lines mean) and the paper plan's T162 line (as amended 2026-10-20, #2086). Nothing here changes a threshold: the bar is read from the `shakedown_span` row.

The ten sessions: 10-22, 10-23, 10-26, 10-27 (drill on `daily`), 10-28, 10-29, 10-30, 11-02 (the first fill session of `main`, which must trade), 11-03, 11-04. The Mac runs the 18:30 ingest each evening and the 08:05 `paper run` each morning, as in scheduling.md. Run every command from the job checkout.

During the span, do **not**: pull a schema bump into the job checkout, run a data release, start a new book, or pull an order-path change (unless it fixes a span blocker).

## 1. Before the span (finish by 10-21)

- [ ] The Mac is on power and logged in, and does not sleep: `pmset -g custom` shows `sleep 0` under "AC Power", and `pmset -g sched` lists no repeating event. If not, run `sudo pmset -c sleep 0` and `sudo pmset repeat cancel` (see [Sleep versus power-off](scheduling.md#sleep-versus-power-off)).
- [ ] Disk has at least 10 GB free: `df -h .`.
- [ ] Both jobs are loaded: `launchctl print gui/$(id -u)/com.tradepartner.ingest | grep "last exit code"`, and the same for `com.tradepartner.paper`.
- [ ] `uv run tradepartner health --check` exits 0 and `uv run tradepartner paper status --all` shows both books (`main`, `daily`) with an open window.
- [ ] Check the New York date first: `TZ=America/New_York date +%F` must print `2026-10-21`. The span opens the session **after** the row's New York date, so a row written on 10-22 opens 10-23 and ends 11-05. Then write the row once:
  ```bash
  uv run tradepartner decision shakedown-span --sessions 10 --order-sessions 5 --reason "T162 shakedown span opens 2026-10-22 (#2086)"
  ```
  The reason names no live strategy. A second row restarts the span, so do not repeat it.

## 2. Each evening, after the 18:30 ingest

- [ ] `uv run tradepartner paper status --all`: the ingest's result is not in it, so also run `uv run tradepartner health --check` (exit 0 is the goal).
- [ ] Look for `stale_data` and `missed_run` alerts in `uv run tradepartner paper status --book main` and `--book daily` (the list at the bottom). No new alert: nothing to do.
- [ ] Look for **an equal-amount dividend group on a held name** (#1613): `uv run tradepartner health` prints "unresolved equal-amount dividend pairs: N" with one line per group (`security@ex-date ticker amount`). Compare each ticker with the book's positions. A new group on a held name: do **not** repair it now (`repair-dividends` needs a data release, barred during the span). Comment on #1613 with the line, and tell the orchestrator.
- [ ] Look for an **announced split, merger, rename or spin-off on a held name, effective next session**. There is no command that lists upcoming corporate actions, so check the news or Alpaca's corporate-actions page for each held name (`paper status --book <b>` shows the count of positions, `positions_daily` has the names). Found one: comment on the span's tracking issue and tell the orchestrator before the 08:05 run; reconciliation is the check that follows (a matching known action does not halt the book, an unknown one is a `mismatch` and restarts the span).
- [ ] If the ingest ended `failed` or `locked`, or the Mac was off: see [Sleep versus power-off](scheduling.md#sleep-versus-power-off). Run `uv run tradepartner ingest` only if the store is not busy; do not backfill without telling the orchestrator.

## 3. Each morning, after 08:05

- [ ] `uv run tradepartner paper status --all`: one line per book. Then, for each book, `uv run tradepartner paper status --book main` and `--book daily`: read "last updated", "kill switch", "reconciliation" and the alerts.
- [ ] What the result means for the span (E.1 and E.2):

| Result | Span effect | You do |
|---|---|---|
| `ok`, reconciliation `ok` | Passes. | Nothing. |
| `no_session` | The day is not a session. | Nothing. |
| `stale` (alert `stale_data`) | Passes **only** with a note. | Write the note below. No resume needed (it does not engage the switch). |
| No run at all (Mac off, job disabled; the `missed_run` alert comes on the **next** session's run) | Passes only with a note on that `missed_run` alert. | Wait for the next morning's run to raise the alert, then write the note. |
| `locked` or `no_window` run | Writes no run row, so it counts as no run. | As the line above; find why (see [exit codes](scheduling.md#what-a-paper-run-exit-code-means)). |
| `halted`, or reconciliation `mismatch` | **Restarts the span** at the session after the `released` row that follows it. A note does not excuse it. | Section "Mismatch or halt" below. |
| `failed` (alert `run_failed`) | Fails E.1 and **cannot be excused by a note**; it does not restart the span by itself. | Fix the cause and resume ([resume steps](scheduling.md#kill-switch-engage-and-the-resume-procedure)). The session stays failed; the span can then only pass if you open a new `shakedown-span` row, which is the owner's decision: tell the orchestrator now. |
| `skipped_kill_switch` | Passes **only** on the drill day (10-27, book `daily`). On any other day it is a real engaged switch. | Find who engaged it (`paper status`: "causes"); resume. |

- [ ] **Write a note** for a `stale` run or a missed session. `paper status` does **not** print the alert id (it prints time, kind, session and message), so read it from the store, read-only:
  ```bash
  uv run python -c "from tradepartner.config import get_settings; from tradepartner.store.db import open_read_only; s=get_settings()
  with open_read_only(s) as c: print(c.execute('SELECT alert_id, book_id, session, kind, message FROM alerts ORDER BY alert_id DESC LIMIT 10').fetchall())"
  ```
  This one-liner is untested; if it fails, stop and ask rather than guess an id (an unknown id is refused). Then:
  ```bash
  uv run tradepartner decision shakedown-note --alert <alert id> --reason "what happened and why it is accepted"
  ```
  Write one note per alert, and only for late data or a missed run.

### Mismatch or halt

It restarts the span. Do not write a note; do not edit a threshold.
1. Read the alert and `~/Library/Logs/tradepartner/paper.err.log`; fix the cause (see [alert kinds](scheduling.md#what-each-alert-kind-means-and-what-you-do)).
2. Resume the book: `uv run tradepartner paper resume --book <book> --reason "root cause and what you checked"` ([full steps](scheduling.md#kill-switch-engage-and-the-resume-procedure)). Resume reconciles first and refuses if that fails.
3. The new span starts at the session after the `released` row. Tell the orchestrator the new end date: ten sessions are needed from there, with the drill and 5 order sessions inside (E.4 reads only the span's rows, so a restart after 10-27 needs a new drill on `daily` inside the new span).
4. Open a new `shakedown-span` row only if you want the count to start later; the restart is automatic.

## 4. 10-27: the drill, on `daily`

Follow [the kill-switch drill](scheduling.md#the-kill-switch-drill-adr-0017-part-e4) exactly: `paper kill --book daily --reason "drill"` after the 10-26 evening ingest and before 08:05 on 10-27; let the scheduled run skip (no `kickstart`); `paper resume --book daily --reason "drill complete: ..."` once it has finished. An `orders` row for `daily`, or any result but `skipped_kill_switch`, is an incident: stop and tell the orchestrator.

## 5. The end (after the 11-04 close and the 18:30 ingest)

The span ends at the last completed session, so run the gate only once 11-04 has closed (the evening of 11-04 or later), never at 08:05 on 11-04: it would see nine sessions and E.1 fails.

- [ ] Every book ran ok on the morning of 11-04 and reconciliation is `ok` (section 3).
- [ ] `uv run tradepartner paper shakedown`. Exit 0 and seven `PASS` lines (E.1 to E.7) is the goal. Check the exit code with `echo $?` directly after.
- [ ] If a line fails, read what it names; fix the cause, write a note if one is allowed, or restart the span. Never edit a threshold.
- [ ] Exit 0: paste the seven lines on #1352 and tell the orchestrator. They go in the Phase 4 retro with the per-book run-time table and the span's `shakedown-span` and `shakedown-note` decisions. Only then does the data-foundation plan's one data sitting run.
