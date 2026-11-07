# Status

**Updated:** 2026-11-07 · **Phase:** 1 closed; 2 Data foundation (175/197), 3 Strategy lab (29/29) and 4 Paper trading (163/197) in build (counts as of 11-07) · **Last tag:** v0.1.0 · **Next tag:** v0.2.0

## Recently done
- Reconciliation names an excess order reading (journaled fills above the broker's filled_quantity) as a bad_reading mismatch, so the book halts and paper status shows it instead of the decision sitting in flight (#3152).
- OPENFIGI_API_KEY is read through Settings (shell or .env) and redacted by secret_values; ingest passes it to map_tickers, so a .env key gets the keyed limits (#3153).
- family_registered_on returns the New York day of the first registration, so an evening registration with holdout.start the next day is forward (ADR 0016 point 4, #3154).
- T382: an operations file may differ from its parent (a sweep variant or H1) in fill price and a higher base cost only, recording parent_fingerprint and differing_keys (#3164)
- T383: `paper cost` measures a book's per-side cost against the open from its own orders; daily window 5 reads +61.4 bp pooled, 60 to freeze (#3160)
- #3162: a non-finite broker filled_quantity is a bad_reading reconcile mismatch and never journals a terminal event (fail closed).
- #3163: T385 step 2 names the real read of a parent's stored frozen set (hypothesis register on the unchanged registered file), not the missing hypothesis show.
- #3169: a non-finite broker filled_avg_price is a bad_reading reconcile mismatch and never journals a terminal event (fail closed)
- T384: `paper run` with no `--book` runs month_end books first, then week_end, then daily (ties by token), so main's buys no longer wait behind daily's run (#3172)
- Data-sitting step 16 now records candidate 5's two store exposures (#2689) with `exposure record`, values from evidence; profitability N 1 -> 3 at the sitting; #3092's open_data record skipped (#3110).

## Teams
Direction: [ADR 0023](decisions/0023-break-first-fast-books-paper-exam.md) (#1740): break first, fast books, paper as the exam. The D5 pause list (T189c, #1598, T187-T187c, T184h, T174c and labeling, T171d, T23, T116c, T141, T204) is stopped; lines stay, no claims. Orchestrator: main checkout, team atlas. Live teams and claims: `team.py status`. Handovers: issue #836.

## In progress
- Paper: daily's window 5 closed 2026-11-06; book `main` (an execution shakedown only, H1 retired) closes window 6 at its 2026-11-09 08:05 stop run. After the sitting both books restart at the open with a measured 60 bp per side (T385; #3134, T382-T384 done).
- Data: the one sitting is Monday 2026-11-09, 17 steps including the owner's checkout pull ([runbook](runbooks/data-sitting.md)).
- Candidates: none. Retired: H1 by its own rule (TP-12, #3117), B10 on 10-27 (TP-11, #3123; T165e still reads ready), published slow signals (TP-10, #3107).
- Live choice: no book reaches it before a forward exam passes (ADR 0016 amendments #3129, #3144, #3148: 12/24/36-month looks, four exams, ledger); T381-T381b next. Rung 1 prices cost at 60 bp (ADR 0023).
- Open PRs: `gh pr list --state open`. On hold: #2124 (live ADR draft), #3136/#3137 (#3131).

## Ready frontier snapshot
Claim through `team.py claim`; read each task line for gates (board-ready is not plan-ready). Agent: T381, T381b, T195 (#1726). Owner: T385 (after 11-09), T197 and T186c (sitting steps), T359, T246, T248f, T343. T165e lists as ready but B10 is retired (#3182). Unclaimed issues: `team.py status`.

## Blocked
T23 and T204 (paused), T146-T149b (after T144's verdict and T145), T163b (after T163 and a passed forward exam), T378 (after T386), T126 (#1351, the owner's call). T214 (#1968) holds until T198's sitting.

## Decisions needed from owner
- Merge when ready (class B): this fold (#3121).
- Confirm or override the 60 bp paper cost (#3134, paper spec item 3) before T385.
- Hold: #2124 (live Phase 6 ADR draft) stays on hold until you lift it.
- The sitting's go, step by step, on 2026-11-09 ([runbook](runbooks/data-sitting.md)).
