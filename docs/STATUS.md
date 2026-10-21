# Status

**Updated:** 2026-10-21 · **Phase:** 1 closed; 2 Data foundation (123/139), 3 Strategy lab (29/29) and 4 Paper trading (109/129) in build (counts as of 10-21) · **Last tag:** v0.1.0 · **Next tag:** v0.2.0

## Recently done
- Trading raw requests use capped exponential backoff for retryable failures (#2094).
- Runbook: the T162 shakedown span day by day (docs/runbooks/shakedown.md), #2095
- Paper plan amendment #2096: T256-T260, the owner's daily-use views (waiting on you, the Trades page, the Risk card), read-only, no schema or config change.
- Paper status shows alert IDs for shakedown notes (#2097).
- B10 sweep file drafted (T165e, #2106): two variants, turnover screen 0.20 beside the no-screen control 1.0; the owner registers and runs it after rung 1 passes.
- T258 session blotter reads per-order fills, status, plan reason, and point-in-time slippage (#2113)
- T260: the Operations page's Risk card shows cash, gross exposure, largest position and drawdown against the window's frozen limits with the headroom left (#2115).
- T256: `ops.waiting_on_owner(conn, settings, now)` lists what each open book waits on the owner for (kill switch, lagging order, missed run, shakedown note, first report, unreadable rows), each with its command (PR #2119).
- Documented the post-T162 one data sitting and its execution gates. (#2126)
- T259 Trades page shows per-session orders, slippage and reasons by book. (#2129)

## Teams
Direction: [ADR 0023](decisions/0023-break-first-fast-books-paper-exam.md) (#1740): break first, fast books, paper as the exam. The D5 pause list (T189c, #1598, T187-T187c, T184h, T174c and labeling, T171d, T23, T116c, T141, T204) is stopped; lines stay, no claims. Orchestrator: main checkout, team atlas. Live teams and claims: `team.py status`. Handovers: issue #836.

## In progress
- Paper: the T162 shakedown span opens 2026-10-22 (row written 10-21; day by day in docs/runbooks/shakedown.md). The drill on the `daily` book is 2026-10-27. Book `main`: first rebalance 2026-10-30.
- Data: one sitting after the span (about 11-05): T197, T186c, #1382 a3, T198, with their execution gates (runbook in #2126).
- Usability pages T256-T260 are merged except the waiting-on-you card on Operations (#2133, ready).
- Kill ladder: B10 is next (sweep file drafted, T165e); the owner registers and runs it after rung 1 passes.
- Open PRs: #2124 live Phase 6 ADR draft (on hold); #2133 T257 waiting card; #2080 spin-off basis spec (ready); drafts: #2123 T261 spin-off on a held name, #2121 action-retract, #2105 T241, #2091 next-session actions, #2084 risk-free default, #1968 T214 release id, #1916 subshare buys.

## Ready frontier snapshot
Claim through `team.py claim`; read each task line for gates (board-ready is not plan-ready). Agent: T195 (#1726), T223, T247 (later). Owner: T165e, T246, T248f, T197, T186c, T71, T159, T161, T75b, T166c; T116c, T141, T184h are paused. Unclaimed issues: see `team.py status`.

## Blocked
T23 (paused), T204 (paused until its trigger), T146-T149b (after T144's verdict and T145), T160-T163c (the shakedown span, then the live ADR), T71b, T126 (#1351 seeded-recall gate, the owner's call). T214 (#1968) holds until T198's sitting.

## Decisions needed from owner
- Merge when ready (class B): this fold (#2135).
- Hold: #2124 (live Phase 6 ADR draft) stays on hold until you lift it.
- Open: #1686 (use API stamps for SGML anchors that 404), #1676 items 3-5 (spin-off and name-change), #1524 (restamp gate before the window's first session), #411 (reconcile zero proceeds on a delisted position).
- #1382: the `restamp-1382a3` store step (owner hold), part of the post-span data sitting.
