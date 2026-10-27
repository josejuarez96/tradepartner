# Status

**Updated:** 2026-10-26 · **Phase:** 1 closed; 2 Data foundation (123/139), 3 Strategy lab (29/29) and 4 Paper trading (109/129) in build (counts as of 10-21) · **Last tag:** v0.1.0 · **Next tag:** v0.2.0

## Recently done
- Paper collect's fill read reaches back to the broker's own submitted_at, so a fast Mac clock no longer hides fills ([#2186](https://github.com/josejuarez96/tradepartner/issues/2186)).
- Paper resume, reconcile and abandon check the broker's account_id against the window's before any broker-derived write ([#2187](https://github.com/josejuarez96/tradepartner/issues/2187)).
- Lot ledger sets are per book: latest-set reads, the changed-set check and lots-reconcile use their own book's set ([#2190](https://github.com/josejuarez96/tradepartner/issues/2190)).
- T224 long/short reporting: the seven metric keys, borrow books kept out of V/N/DSR, page exposure and proxy limits, bt long/short oracle case (#2198)
- paper status --all opens with one plain line per book, owner times show ET, next rebalance says data day, and positions say the broker already holds them ([#2201](https://github.com/josejuarez96/tradepartner/issues/2201)).
- health: a plain verdict line first on `health` and the Data health page (data checks and how far bars are behind; one-session gap expected before the 18:30 ET ingest) ([#2202](https://github.com/josejuarez96/tradepartner/issues/2202))
- paper check and paper shakedown print owner-readable lines (progress, date range, plain no-span message); query/thresholds behind --verbose (#2204)
- Runbooks: alert table lists all 19 alert kinds, data-releases opener matches the store-backed record, window-restart and key-rotation runbooks added (#2205).
- Operations page: the stale badge says it is normal before the morning run, and Reconciliation lists events on holdings in plain lines (raw JSON in an expander) ([#2206](https://github.com/josejuarez96/tradepartner/issues/2206)).
- Paper settle reads broker fills from submitted_at so clock skew cannot hide them ([#2216](https://github.com/josejuarez96/tradepartner/issues/2216)).

## Teams
Direction: [ADR 0023](decisions/0023-break-first-fast-books-paper-exam.md) (#1740): break first, fast books, paper as the exam. The D5 pause list (T189c, #1598, T187-T187c, T184h, T174c and labeling, T171d, T23, T116c, T141, T204) is stopped; lines stay, no claims. Orchestrator: main checkout, team atlas. Live teams and claims: `team.py status`. Handovers: issue #836.

## In progress
- Paper: the T162 shakedown span opened 2026-10-22 (day by day in docs/runbooks/shakedown.md). The drill on the `daily` book is 2026-10-27. Book `main`: first rebalance 2026-10-30. The owner's checkout pull is planned for 2026-10-28 (store backup, pull, v35→v36 migration).
- Data: one sitting after the span (about 11-05): T197, T186c, #1382 a3, T198, with their execution gates (runbook in #2126).
- Kill ladder: B10 is next (sweep file drafted, T165e); the owner registers and runs it after rung 1 passes.
- Building: T267 (dividend on its pay date, fast-tracked before the November pay dates; Codex) and the Home/Book screens T270-T279 (plans #2221 and #2230 merged 2026-10-26).
- In review: #2224, #2229, #2227 (drafts). On hold: #2124 (live Phase 6 ADR draft). Other open PRs: `gh pr list --state open`.

## Ready frontier snapshot
Claim through `team.py claim`; read each task line for gates (board-ready is not plan-ready). Agent: T195 (#1726), T223, T247 (later). Owner: T165e, T246, T248f, T197, T186c, T71, T159, T161, T75b, T166c; T116c, T141, T184h are paused. Unclaimed issues: see `team.py status`.

## Blocked
T23 (paused), T204 (paused until its trigger), T146-T149b (after T144's verdict and T145), T160-T163c (the shakedown span, then the live ADR), T71b, T126 (#1351 seeded-recall gate, the owner's call). T214 (#1968) holds until T198's sitting.

## Decisions needed from owner
- Merge when ready (class B): this fold (#2236).
- Hold: #2124 (live Phase 6 ADR draft) stays on hold until you lift it.
- Open: #1686 (use API stamps for SGML anchors that 404), #1676 items 3-5 (spin-off and name-change), #1524 (restamp gate before the window's first session).
- #1382: the `restamp-1382a3` store step (owner hold), part of the post-span data sitting.
