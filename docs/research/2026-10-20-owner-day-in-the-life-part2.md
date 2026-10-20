# Research Report: A day in the life of the owner, Part 2: the first day with real trades

**Brief:** the orchestrator's re-walk brief (publication issue #2009)  ·  **Date:** 2026-10-20, walked between 14:14 and 14:26 UTC (10:14 to 10:26 ET, market open)  ·  **Status:** COMPLETE  ·  **Agent/model:** team rewalk  ·  **Checkout:** the owner's main checkout at `9d776b2d` (pinned, untouched)  ·  **Part 1:** [2026-10-19 owner day in the life](2026-10-19-owner-day-in-the-life.md)

**What happened today.** The daily book's 08:05 run sent 202 buy orders, about $494 each. Alpaca filled them at the 09:30 open, about $99.8k in all. Our system records fills only at the next run, tomorrow at 08:05. So today is the first day where Alpaca and our system disagree about what the owner owns. This walk asks what the owner sees today.

**How this was walked.** Read-only only: `paper status --all`, `paper status --book daily|main`, `paper check --book daily|main` (opens the store read-only; confirmed in the code), `paper shakedown`, `health`, `health --check`, `lab status`, `launchctl list`, and `--help` screens. I started the dashboard, screenshotted all six pages plus Operations with the `daily` book selected, and then stopped it. `pgrep` confirmed that both streamlit and headless Chrome had exited by 14:25 UTC. I did not run `paper report`, because at this commit it writes trials. I did not open Alpaca or read `.env`. The orchestrator's scratch screenshots and raw outputs hold the evidence.

## Answer

**Verdict:** MIXED  ·  **Confidence:** medium (Alpaca not opened; fills not yet in our records)

- **All day, our system shows a book that does not exist.** The owner holds 202 stocks at Alpaca. Every one of our screens says: 0 positions, 202 open orders, cash $100,000.00, no fills. That is true to our records, which stop at 08:22 ET. But no screen says "fills are recorded tomorrow at 08:05", so it looks wrong. This is new, and it is now the top friction.
- **The names are numbers.** The Signal ranking card and the Order chain card show `0002023554`, not a ticker. The `decision_reason` column reads `None` on every row. Matching our 202 orders against Alpaca's list by hand is not possible.
- **Performance still has no home** (Part 1's #1). It now matters more, because $99.8k is invested. Alpaca's chart is the only place to see the day's gain or loss.
- **Some of Part 1's alarms are gone.** The `stale: no run yet` badge on Operations is gone, both books say `last run ok`, the launchd jobs show last exit 0, and Data health no longer shows `Coverage 0.0%`. New confusing wording has replaced some of them (below).
- **The re-ranked list** puts "see today's trades today" first, then performance, then tickers. Away-from-Mac alerts stay in the top 4.

## Findings by routine

Ranks shown as "P1 #n" refer to Part 1's top-11 friction list in section (a).

### 1. Morning check: did it all work, is anything waiting on me?
**What he sees.** `uv run tradepartner paper status --all`:
> `book daily: window 5 open; positions 0; open orders 202; kill switch released; last run ok; next rebalance 2026-10-20`
> `book main: window 6 open; positions 0; open orders 0; kill switch released; last run ok; next rebalance 2026-10-30`

`launchctl list | grep trade` shows `com.tradepartner.paper` and `com.tradepartner.ingest`, both with last exit `0`. On the Operations page the `stale` badge from 10-19 is gone, and the kill switch reads `ok`.

**What is missing or confusing now.**
- At 10:14 ET it still says `open orders 202` and `positions 0`, though Alpaca filled them at 09:30. Nothing says these numbers are as of 08:22 ET and that fills land tomorrow. The `as of 2026-10-20 12:22 UTC` stamp is there, but only in UTC, and it does not explain itself.
- `next rebalance 2026-10-20` reads as "today". It is the rebalance planned from today's close and traded tomorrow morning. The word "rebalance" means the data day, not the trading day.
- `paper shakedown` fails: `failed: ValueError: no shakedown_span decision: open the span with tradepartner decision shakedown-span …`. This is something waiting on the owner (T162). He only finds it by running that command, and it reads as a crash.

**Still matches Part 1?** Partly. The badges improved. The "one line plus waiting on me" gap stands (P1 #4). The new gap is the time lag: the morning check happens before the trades it should report.

### 2. Portfolio performance
**What he sees.** `paper status --book daily`: `positions 0 (value 0.00)`. Operations, Reconciliation: `broker cash: 100,000.00` (daily) and `100,008.88` (main), both as of 08:22 ET. There is no equity, gain or loss, chart, or comparison with SPY. `paper check --book daily`: `FAIL tracking: no paper_reports row yet`.

**What is missing.** Everything from Part 1, plus the lag. Even tomorrow, our system will show the positions at their fill prices, not their value now. The Backtest page now opens on **Trial #20**, a `tracking` trial the 08:05 run wrote. It shows `No rows to plot` four times and `Today's raw DSR not recomputed: 'sharpe_period'`, which is an error name, not a sentence.

**Not checked.** `paper report --book daily` (it writes trials at this commit, #1923). Alpaca's own chart (not opened).

**Still matches Part 1?** Yes, still P1 #1. It now matters more: real money is in, and there is no way to see today's gain or loss outside Alpaca.

### 3. Trades by day
**What he sees.** Operations, daily book: `Fills: No fills yet in this window.` The Order chain card shows one order at a time from a dropdown, for example `tp-daily-20261020-0002041385-buy-1`, with the steps `order buy 494.3 R…` (cut off), `pending` at 12:10:47 UTC, and `accepted` at 12:15:59 UTC, and then a note: `Some steps are not shown; the window has more than the row limit.` The Signal ranking table has `security_id, rank, score, signal_reason, decision, decision_reason, selected`. Every row shows `selected / trade / None`, keyed by number (`0002023554`, rank 1, score 7.7287). The chart's x-axis is 202 overlapping ID labels and cannot be read.

**What is missing.** Today's 202 fills (not recorded until tomorrow). Tickers. A reason that says something (`decision_reason` is `None`, and `signal_reason` only says `selected`). The order id has the security id in it, not the ticker. Even after tomorrow's run, a 202-row day has no blotter: just a capped list plus one order at a time.

**Still matches Part 1?** Yes (P1 #8), but worse than Part 1 thought. Part 1 assumed the cards would carry symbols. The ranking and order cards do not, so Alpaca's list cannot be matched to ours by eye.

### 4. Something went wrong
**What he sees.** `alerts (0)` on both books, and `No alerts in this window.` Nothing went wrong today, so this was not exercised. The code's default alert channels are `("store", "macos")` (`config.py:1912`).

**Not checked.** Whether `.env` turns on email (I may not read it). Whether a notification would arrive. How long a resume takes.

**Still matches Part 1?** Yes (P1 #2 and #3). Unchanged, and now with $99.8k exposed while the owner may be away from the Mac.

### 5. A new strategy idea
**What he sees.** `lab status`: `momentum: N 11`, `open sweeps: momentum-topfrac-cadence … 0 of 6 counted … 6 stale; incomplete (stale)`, and `development boundary: none`.

**What is new.** Nothing for this routine. The 202 trades do not change it.

**Still matches Part 1?** Yes (P1 #6 for experiments). Lower priority than the trading-day items.

### 6. Reading a result
**What he sees.** The Trial registry now lists `20 trials, newest first: 1 failed, 12 ok, 1 refused_window, 6 unfinished`. The newest three are trial 20 (`tracking`, ok, written by the 08:05 run), 19 (`refused`, from 10-19's `paper report`) and 18 (`failed`, the same). `N today 11` is unchanged, so the tracking trials appear not to count in N.

**What is new.** The daily book writes one `tracking` trial on every rebalance (`execution/planning.py` writes `kind=_TRACKING` for each plan). That is about 250 rows a year in the trial list. The Backtest page opens on the newest one, which is always an empty one-day trial. A real backtest result will be buried a page down.

**Still matches Part 1?** Yes, with a new sub-item: the list fills up with tracking trials. The reading-layer gap (P1 #5) stands.

### 7. Weekly review
**What he sees.** `paper check --book daily`:
> `FAIL rebalance_count: 0 scheduler-executed rebalance session(s) [], need >= 63; no-trade fill-session runs, listed not counted: [2026-10-20] (query: …)`
> `FAIL tracking: no paper_reports row yet (query: …)`
> `PASS chain: …` · `PASS override_reason: …`

**Confusing now.** The 10-20 run, which placed 202 trades, is listed as a "no-trade fill-session run". It counts as "no trade" only because its fills are not recorded yet. Each line ends with a long `query:` clause meant for an auditor. It is correct that the book needs 63 rebalances (about three months), but the output does not say "1 of 63 so far, about three months to go".

**Still matches Part 1?** Yes (P1 #4 and #10), plus a wording bug.

### 8. Risk and capital
**What he sees.** Nothing for today. Our records show 0 positions, so exposure is 0% and cash is $100k. The real figures are about 99.8% invested and roughly 0.5% in each of 202 names, far inside `max_position_weight` 0.05.

**Still matches Part 1?** Yes (P1 #7). It now matters for real: the drawdown limit (30%) is live with money in, and no screen shows the distance to it.

### 9. Holdings events (dividends, splits, mergers)
**What he sees.** Reconciliation: `{"activities": [], "allowed": [], "explained": [], "lagging": [], "mismatches": [], "pending": [], "symbol_changes": []}` (raw JSON, still).

**Not checked.** No event on a held name yet. Our records hold none. With 202 names, the first dividends will arrive within days.

**Still matches Part 1?** Yes (P1 #8). It is now on a clock: the first unexplained activity halts the book.

### 10. Costs: real slippage against the modelled 15 bps
**What he sees.** `paper status --book daily`: `slippage vs open (bp, adverse positive, per order): buy n=0; sell n=0; all n=0; no bar 0; off session 0; pending 0`.

**Not checked.** No fills are recorded. The first numbers arrive after tomorrow's run. `pending 0` while 202 orders are open is odd. It probably counts only orders past their fill session, which I did not confirm.

**Still matches Part 1?** Yes (medium).

### 11. Attribution
**Not checked.** It needs `paper report`, which I may not run. Unchanged from Part 1.

### 12. Lifecycle: promote, compare books, stop
Unchanged. `lab status`: `momentum-daily-shakedown … operations book of momentum-topfrac-cadence v1, not promoted`. The books table still shows only the operational columns, nothing side by side.

### 13. Research pipeline
Unchanged. The sweep shows as `incomplete (stale); 6 stale` on the Trial registry page and in `lab status`, with no sentence saying whether that matters. Part 1 quoted its retirement reason, so it seems to be harmless history. It still reads as a fault.

### 14. Data trust
**What he sees.** `health --check` exits 0 but prints **14,233 lines**, most of them security-id lists. The last lines:
> `warning: the last EDGAR run reports 1 quarantined accessions …`
> `warning: 1 listed duplicate dividend keys are pending repair (tradepartner repair-dividends); each still counts its payment twice`

The Data health page: `Coverage 74.6%`, `Live names with no bar on 2026-10-19 (1348)`, and every integrity check green, **including `duplicate_dividends: pass`**.

**Confusing.** The page says duplicate dividends pass, while the CLI says one is pending and counts twice. Neither says whether this affects tomorrow's run. "Exit 0, 14,233 lines" is not a verdict.

**Still matches Part 1?** Yes (P1 #4). The 0.0% figure from Part 1 is gone. The missing verdict line and the contradiction are now the issue.

### 15. Software upkeep
**Not walked.** I was forbidden to pull or switch branches. The checkout is pinned at `9d776b2d` while main moves on. Unchanged from Part 1 (P1 #9).

### 16. Working with agents
**Not walked** (GitHub-side). The shakedown span that only the owner can open (routine 1) is the one "waiting on me" item found today, and no screen shows it.

### 17. Being away
**Not checked** (no phone and no alert channel test). It matters more now: a 202-name book runs every session, and the stop path is `paper kill --all` on the Mac only.

### 18. Records and taxes
Low now. The 202 buys will become 202 tax lots tomorrow, but this is paper. Unchanged.

### 19. Going live
`paper shakedown` cannot pass yet: the span decision is not recorded (see routine 1). That is the gate for live. Unchanged otherwise.

### 20. Broker admin, 21. Recovery, 22. Running costs
**Not walked.** Nothing in today's trades touches them, and the read-only rules forbid exercising them.

## Part 1 checklist (section c), item by item

| Item | Result today |
|---|---|
| Note each window's start before reading Alpaca | daily: window 5, first rebalance 2026-10-19 (orders 10-20). main: window 6, first rebalance 2026-10-30. Alpaca not opened |
| `paper status --all` shows positions and `last run ok`; `stale` badge gone? | `last run ok` yes. Positions **no** (0, fills tomorrow). Badge **gone** |
| Fills card lists the 10-20 fills; match Alpaca line for line? | **No fills yet.** Re-check after the 10-21 08:05 run. A line-for-line match is not possible by eye: our cards show security ids |
| Signal ranking `decision_reason` explains each name? | **No.** `None` on every row, `signal_reason` = `selected`. Whether it survives the 10-21 replacement: not yet checkable |
| `paper report --book daily` runs? Still writes a trial? | **Not run** (forbidden at this commit) |
| Alpaca equity vs our system for the same day | Ours: $100,000.00 cash, 0 positions. Alpaca: not opened. The gap is the whole book |
| First alert or halt | None today |
| Exposure, largest weight, drawdown against `risk.*` | Nowhere on screen |
| First dividend or corporate action | None yet |
| Slippage vs 15 bps | `n=0`. Re-check after the 10-21 run |
| After five fill sessions, `paper check` readable? | Too early. Today's wording is already confusing ("no-trade" for the 202-order run) |

## Re-ranked top-10 frictions

"Order path" means it touches code that places, collects or reconciles orders, so it needs `safety-reviewer`. Sizes: S (a day or less), M (a few days), L (a week or more).

| # | Friction (routines) | What to build (one line) | Size | Order path? | Owner decision? | vs Part 1 |
|---|---|---|---|---|---|---|
| 1 | Our system is blind to today's trades until tomorrow 08:05; every screen shows 0 positions, 202 open orders and $100k cash during the day (1, 2, 3, 8) | First, label the lag on `paper status` and Operations ("fills are recorded at the next 08:05 run"). Then add a read-only broker peek (positions and value now), or a fill-collect job after the open | S label; M peek or job | Label: no. Peek: reads the broker, not orders (safety review). Collect job: **yes** | **Yes:** a read-only peek, or a second scheduled collect after the open | **New** |
| 2 | No performance view: equity, today's gain or loss, vs SPY, vs backtest (2, 10, 11) | A Performance card per book: equity and gain/loss since the window opened, vs SPY, then vs its backtest (`paper_report_periods`), plus a fix for #1923 | L | No | Yes: Streamlit now or wait for the ADR 0018 app | Was #1; still top tier |
| 3 | Security ids instead of tickers; `decision_reason` `None`; unreadable ranking chart; order ids carry ids (3, 6) | Show the ticker beside or in place of `security_id` on every Operations card and the Weights table; fill or hide empty reason columns; drop the 202 axis labels | S | No (display only) | No | Was part of #8; **moved up** |
| 4 | Away from the Mac: no alert, no stop (4, 17) | Configure and test the email channel (owner sets `ALERT_EMAIL_*`), then decide on a phone kill path | S email; M phone | Kill path: **yes** | **Yes** (email is his step; phone needs a decision) | Was #2; same |
| 5 | No one-line morning answer and no "waiting on me" list; the shakedown span sits unseen; `next rebalance` reads as today (1, 7, 16, 19) | A top line on `paper status --all` and Operations: "All OK / N things need you", listing the shakedown span; reword `next rebalance` as "next plan (trades on …)" | M | No | No (wording only) | Was #4; same |
| 6 | Halt diagnosis needs an agent; runbook entries missing (4, 9) | Runbook entries for each alert kind and for the 404 and lot-ledger halts; plain-words halt reasons | M | No (docs and messages) | No | Was #3; down slightly (no halt yet with fills) |
| 7 | No risk headroom: invested %, largest weight, drawdown vs `risk.*` (8) | A Risk card: each limit, the value now and the room left | S–M | No (read-only) | No | Was #7; more urgent now |
| 8 | No daily blotter; corporate events shown as raw JSON, with 202 names about to see dividends (3, 9) | A per-session fills table (ticker, side, shares, price, slippage vs open, reason) with a date picker; an "events on my holdings" list | M | No (reads the journal) | No | Was #8; same |
| 9 | Wording and clutter that look like faults: "no-trade fill-session runs" for the 202-order run, the `query:` clauses, the tracking trial on every run that the Backtest page opens to, `not recomputed: 'sharpe_period'`, the `incomplete (stale)` sweep (6, 7, 13) | Reword the `paper check` lines with progress ("0 of 63 rebalances"); hide tracking trials by default and open on the newest real backtest; replace error names with sentences | S | No | No | **New** |
| 10 | Data trust has no verdict: `health --check` is 14,233 lines; the page says `duplicate_dividends: pass` while the CLI warns one counts twice (14) | One verdict line at the top of `health --check` and on Data health ("fine for tomorrow's run" or what is not); make the page and CLI agree on duplicate dividends | S | No | No | Was part of #4; split out |

**Dropped below 10:** result verdict sentences and experiment verdicts (P1 #5), experiment scripts (P1 #6), the upkeep and recovery runbooks (P1 #9), the research and lifecycle screen (P1 #10), and taxes, keys and spend (P1 #11). None of them changed today, and the live book's needs come first.

## Not checked
- **Alpaca's dashboard:** not opened (no access from this session). Every "Alpaca says" figure comes from the brief: 202 fills at 09:30, about $99.8k.
- **`paper report`:** forbidden (it writes trials at `9d776b2d`, #1923).
- **Fills, slippage, events and the post-replacement ranking:** not in our records until the 10-21 08:05 run. Re-walk items 3, 4, 9 and 10 of the checklist after it.
- **Alert delivery and the email channel:** `.env` may not be read, and no alert fired.
- **Whether tracking trials count in N:** `N today 11` with 20 trials suggests not. Not confirmed in code.
- **What `pending 0` in the slippage line counts while 202 orders are open:** not confirmed.
- **Routines 15, 16, 20, 21, 22:** not exercisable read-only today.

## Sources
- [Part 1: the owner's day in the life](2026-10-19-owner-day-in-the-life.md), including its Part 2 checklist.
- The orchestrator's scratch screenshots and read-only CLI outputs from the 2026-10-20 walk, as described above.
- The orchestrator's brief for the 202 fills and approximately $99.8k invested; Alpaca's dashboard was not opened.
- Code at the pinned checkout `9d776b2d`: `src/tradepartner/dashboard/ops_page.py`, `src/tradepartner/cli.py`, `src/tradepartner/execution/planning.py`, and `src/tradepartner/config.py`.
