# Runbook: restarting a paper window

Owner-run, on the real store, with the scheduled jobs stopped as in [scheduling.md](scheduling.md). A restart means closing a book's paper window and opening a new one for it. It is not a pause. The paper record starts again. Four restarts so far (10-13 and 10-18) each did this, and nothing told the owner what went with it ([owner walkthrough](../research/2026-10-19-owner-day-in-the-life.md), item 37).

Code references: `execution/window.py` (`start`, `stop`, `abandon`), `cli.py` (`paper start | stop | abandon`).

## What a restart resets

A window is the unit the paper record is built on. A new window is a new `window_id`, so these start again from zero:

- **The count toward the exit criteria.** `paper check` and `paper report` read only the book's **latest** window (`execution/check.py`). Rebalances already executed in the old window do not count in the new one, and `paper.min_rebalances` starts at 0.
- **The tracking comparison and the drawdown peak.** Both are per window. The new peak is the new window's starting equity.
- **The starting cash and equity**, read from the broker account at `paper start`.
- **The first rebalance session (T_0)**, recomputed as the first rebalance session after today.
- **The frozen settings.** `risk.*`, the six `paper.*` keys, costs and execution are frozen again from the current config. A config change you made since the old window opened takes effect here, and `paper start` refuses if the live costs or fill price differ from the hypothesis's registration (`costs_drift`, `execution_drift`).
- **The kill switch.** It is derived per window (scheduling.md, "Several books"), so the new window starts with it off. Resume the old one first only because `paper stop` needs that.
- **The shakedown checks.** They are read per window: a window counts on the sessions it was open (`execution/shakedown.py`), so the new window's sessions start from its own first run.

These do **not** reset:

- **The Alpaca account and its chart.** Alpaca's portfolio chart is per account, so it spans every window the book has had, unless you reset the account at Alpaca (an `abandon` path, below).
- **The trial registry, N, and the store's history.** Old windows stay in `paper_windows` and `paper_window_stops`.
- **Residues of a normal close.** A `closed` window lists the `dust` and `untradable` names it could not sell, and the next `paper start` accepts them only if the broker holds exactly those. After an `abandoned` window nothing is carried: the account must be strictly flat.

## Checklist

1. **Say why** in one line, and note which book. Every command takes `--book <token>` (default `main`).
2. **Stop the jobs** that could run during the restart (scheduling.md, "Before pulling a schema migration"). A paper run in the middle of a stop or start would hit the run lock (`locked`).
3. **Read the state.** `uv run tradepartner paper status --book <token>`. If the kill switch is engaged, `paper stop` is refused: `paper resume --reason "..."` first (it reconciles first).
4. **Close the old window.** Normal path, in two steps:
   ```bash
   uv run tradepartner paper stop --reason "why" --book <token>   # requests the stop
   # the next paper run (or a manual one) sells the holdings; then:
   uv run tradepartner paper stop --reason "why" --book <token>   # closes the window
   ```
   If `stop` cannot finish, use `paper abandon` and follow "`abandon`: the last resort" in scheduling.md, including the Alpaca paper-account reset.
5. **Check the account is ready.** After `closed`: no open orders, and the positions are exactly the listed residues. After `abandoned`: zero positions, zero open orders. If you reset the account at Alpaca, check whether the account id or the keys changed; if the keys changed, follow [key-rotation](key-rotation.md) (`paper start` records whatever account id the broker reports for the new window; only another book's account is refused).
6. **Open the new window.**
   ```bash
   uv run tradepartner paper start --book <token> --hypothesis <slug>
   uv run tradepartner paper status --book <token>
   ```
   A refusal names its reason (`not flat`, `window_open`, `account_in_use`, `costs_drift`, ...). Fix the cause; do not work around it.
7. **Restart the jobs** and check the next scheduled run ended `ok` ("Checking a run" in scheduling.md).
8. **Note the new window id and what the old one ended with** (its stop reconciliation and residues) on the issue or in the change log, so the break in the record is written down.

If a shakedown span is open, tell the orchestrator before step 4: a restart in the middle of a span changes which sessions can count ([shakedown.md](shakedown.md)).
