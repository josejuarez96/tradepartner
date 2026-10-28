# Runbook: the sandbox book (regression runs against the real Alpaca paper API)

Book `sandbox` is the third Alpaca paper account (`PA32VP9UD`). It exists to test fixes and run regressions against the real paper broker (owner decision 2026-10-28). It is **never graded and never part of the [ADR 0017](../decisions/0017-fast-paper-and-machine-readiness-gate.md) shakedown**. It lives only in a throwaway COPY of the store, so the real store never knows it: the scheduler, the heartbeat, the alerts, `paper status --all`, `paper shakedown` and Home never see it. Code: `execution/lock.py` (`sandbox_store_refusal`), `config.py` (`SANDBOX_BOOK_ID`).

## One-time step (the owner)

In `.env`, add the same two values that `ALPACA_SANDBOX_API_KEY` / `ALPACA_SANDBOX_API_SECRET` hold, under the book's names (keep the old pair; the probe scripts read it):

```
ALPACA_PAPER_BOOKS__SANDBOX__API_KEY=<copy of ALPACA_SANDBOX_API_KEY>
ALPACA_PAPER_BOOKS__SANDBOX__API_SECRET=<copy of ALPACA_SANDBOX_API_SECRET>
```

Nothing else is declared: a book is just a token with a pair. The adapter refuses a pair that equals the data keys, `main`'s or another book's, so a pasted wrong value fails with `no_credentials` or a clash message that names variables only.

## What keeps it away from the real book

| Guard | Where it comes from |
|---|---|
| The sandbox refuses to run unless `store.path` resolves under the system temp directory (or `/tmp`), symlinks resolved. The live store is never there. A refusal is exit 2 on every `paper` command with `--book`, and `build_broker` refuses before a key is read. | `lock.resolve_book`, `brokers.build_broker`, tests in `tests/execution/test_sandbox_book.py` |
| Account check (#2187): `resume`, `abandon` and `reconcile` compare the broker's `account_id` with the one the window recorded at `paper start`, and refuse on a mismatch. So the sandbox's keys cannot settle another book's window, nor another book's keys the sandbox's. | `execution/window.py`, `resume.py`, `reconcile_run.py` |
| A book is run only from the store it is in. The scheduled `paper run` (no `--book`) runs the live store's open windows, and the sandbox has none there. The plist and `ingest` read the live `data/tradepartner.duckdb`, never the copy. | `scheduling.md`, "Several books" |
| `paper run --book X` by hand only ever pings the heartbeat `/fail`, never OK (below). | `heartbeat.py` |
| Keys: the sandbox pair is read only for book `sandbox`. No book falls back to another's pair. | `adapters/alpaca_trading_raw.py` |

Every command takes `--book` and the copy also holds the owner's real books' windows: **always pass `--book sandbox`**. A bare `paper run` on the copy would run the real books' open windows with their real keys.

## Regression run

Work in your team folder, on the branch under test. Use the placeholders below; never open or read the live store yourself.

1. **Copy the store, only when nothing runs.** Ask the owner to confirm no `paper run`, `ingest` or dashboard is up (`uv run python scripts/team.py status`, and scheduling.md "job windows"; a copy taken during a write can be torn). The owner copies, or you copy with the same rule:
   ```bash
   SBX=$(mktemp -d /tmp/sandbox-XXXXXX)
   cp <live store path> "$SBX/store.duckdb"
   ```
2. **Point everything at the copy and silence the owner's channels.** One shell, every command:
   ```bash
   export STORE__PATH="$SBX/store.duckdb"
   export ALERT_HEARTBEAT_URL_PAPER= ALERT_HEARTBEAT_URL_INGEST=
   ```
   Blank heartbeat URLs mean no ping at all. If `alerts.channels` includes `email`, also `export ALERTS__CHANNELS='["store"]'`. Check `echo $STORE__PATH` is under `/tmp` before the next step.
3. **Check out the branch under test** in a team folder (`team.py start <name>`), and `uv sync` there. All commands below run from it.
4. **Flatten leftover sandbox positions.** The account is shared across runs. In the Alpaca paper dashboard (owner), close every position and cancel every open order for `PA32VP9UD`, or run `paper abandon --book sandbox --reason "reset"` if a previous copy's window is in this copy. `paper start` needs the account strictly flat of anything it does not own.
5. **Start.** Use a registered, signed-off, small test strategy. The copy already holds `momentum-daily-shakedown` (the daily machine-test book, rebalanced every session):
   ```bash
   uv run tradepartner paper start --hypothesis momentum-daily-shakedown --book sandbox
   ```
   Note the printed window id and `account_id`: it must be `PA32VP9UD`'s.
6. **Run the scenarios**, inside the submit window (scheduling.md, "The submit window"), each followed by `uv run tradepartner paper status --book sandbox`:

   | Scenario | How | Expected |
   |---|---|---|
   | Submit | `paper run --book sandbox` | orders sent once, each with a `client_order_id`, journal rows `submitted`, no duplicates on a repeat run |
   | Partial fill | a thinly traded name or a tiny `qty` limit in a test branch; or read a partial from the journal | `partially_filled` then `filled`, quantities add up, reconciliation `ok` |
   | Cancel | `paper stop --book sandbox` with an order open, then `paper run --book sandbox` | the order is cancelled and recorded once, the window `closed` after the stop run |
   | Reconcile | `paper reconcile --book sandbox` | status `ok`, broker cash and positions equal the ledger |
   | Kill and resume | `paper kill --book sandbox --reason test`, `paper run --book sandbox` (ends `skipped_kill_switch`), `paper resume --book sandbox --reason test` | no order sent while killed, resume reconciles first and clears the switch |
   | Report | `paper report --book sandbox` | ends at the last marked session, refuses before any trial |

   Compare each outcome with the Expected column and with the fix's own acceptance criteria; write the exact commands, outputs and verdicts in the PR.
7. **Leave the account flat.** `paper stop --book sandbox` and a stop run, or the dashboard close-all (owner). Check `paper status --book sandbox` shows no position.
8. **Delete the copy** (owner, or you for a directory you made under `/tmp`): `rm -r "$SBX"` and `unset STORE__PATH ALERT_HEARTBEAT_URL_PAPER ALERT_HEARTBEAT_URL_INGEST ALERTS__CHANNELS`. A copy kept around is a store with live keys' windows in it; do not reuse it.

## Rules

- Never point `STORE__PATH` at the live store; the code refuses, but do not try.
- Never run the sandbox from the live checkout's `data/`, and never copy a sandbox store back.
- The sandbox's results are evidence about the code, not about any strategy: nothing from it enters the trial registry as a graded result or the shakedown span.
- If a run halts, `paper resume --book sandbox` as in scheduling.md, or abandon and delete the copy; a halt here never needs the owner's `main` tools.
