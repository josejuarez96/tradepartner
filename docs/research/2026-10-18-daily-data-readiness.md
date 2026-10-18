# Research Report: Daily-strategy data readiness, a reconciliation of existing evidence

**Brief:** #1833 (owner brief, trimmed by the orchestrator to one file)  ·  **Date:** 2026-10-18  ·  **Status:** COMPLETE (probes Q1 and Q2 measured 2026-10-18)  ·  **Agent/model:** team dailyready, claude-opus-5-5  ·  **Repo at:** `0eeac1b7`

**Question.** What can TradePartner credibly test now with daily and weekly strategies? What specifically blocks the next experiments, and what is the least extra work needed?

**Method.** This report reconciles existing work. It discovers nothing new. Sources: the repo docs, plans checked against merged commits, issues and PR comments (#836, #969, #1382, #1542, #1551, #1598, #1682, #1743, #1747, #1748, #1792), and the code on `main`. Nothing here was run against the store.

**Answer in one line.** The data is not what blocks the next experiment. The one live candidate, B10 short-term momentum, runs on data and code the repo already holds. It waits on three things: fills (rung 1), one engine task (T217c), and one owner sitting (T198). Its economic prior is near zero after costs. Every other daily or weekly strategy was already rejected or parked, for economic reasons, before data could matter. No new data source or data task is justified.

Evidence states used below:
- **RES**: researched.
- **DEC**: decided.
- **IMPL**: implemented.
- **FIX**: tested on fixtures only.
- **STORE**: store-validated, meaning an owner-run read with recorded numbers.
- **OPS**: validated in operation.
- **DEF**: deferred.
- **UNRES**: unresolved.

---

## A. Evidence reconciliation

### A1. Traceability matrix

| # | Research question | Existing evidence | Prior finding | Decision | Implementation | Validation | Remaining uncertainty | Strategy impact | Action |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Which short-horizon strategies have a chance after costs? | [short-horizon report](2026-10-09-short-horizon-candidates.md) (#1353) | No candidate is SUPPORTED long-only after costs. EAR drift and IRRX are MIXED; STMOM is INSUFFICIENT; plain reversal and industry momentum are NOT SUPPORTED. | Ranks 1-2 filed (#1358). Kill ladder order B10 → B9 → IRRX ([ADR 0023](../decisions/0023-break-first-fast-books-paper-exam.md)). | n/a | RES | Every figure is long-short and gross. The Chen-Zimmermann (CZ) numbers are the team's own scratch computation, unchecked. | Sets the candidate list | reuse |
| 2 | Does dropping long-only help? | [beyond-long-only](2026-10-10-beyond-long-only.md) (#1452); [B11](2026-10-10-b11-beta-alpha-split.md) | NOT SUPPORTED or INSUFFICIENT for every book. Short-leg alpha sits in names that are expensive to borrow. Retail accounts get no rebate. A hedge adds no edge (LS-13). B11: no trial's alpha differs from 0; H1's holdout "win" was beta 1.92. | Shorting is forbidden until an ADR. B12 was proposed and never run. | B11 done (a query). T211's metrics half reports `beta_spy` and `alpha_spy_annual` (#1791). | RES; B11 is STORE (read on the trial tables) | B12 never ran | Hedged and long-short books are deferred | defer |
| 3 | B9 earnings-reaction drift | [desk note](2026-10-18-b9-kill-b3-retirement.md) (#1765) | Size-screened long leg +0.07 %/mo (t 0.94). Break-even 1.75-3.5 bp per side. | **Killed** at rungs 1-2 by the owner, 2026-10-18 | n/a | DEC (economic, on published returns; no store read) | None material. The VW file would pass, but it was set aside in advance as a forking path. | Removes the main consumer of 8-K data | reuse; do not reopen |
| 4 | 8-K Item 2.02 with acceptance times | Data-foundation plan T164-T164e; claim TP-1 | TP-1 (UNGRADED): a scratch copy of the store, using cached submissions, found 97.9-99.3 % of universe CIKs with a 2.02 8-K each quarter, 2020Q3-2023Q4. Median lag to the 10-Q/10-K is 1.0 day. About 50 % are accepted 16:00-17:30 NY. | T164e's real-store run waits for its only remaining consumer, IRRX, which is parked behind T198 | `filing_events` table (schema 21, #1395); adapter keeps `items` (#1428); as-of read (#1426); ingest code (#1459, #1471). Switch `edgar.filing_events_enabled = false`. | IMPL + FIX. **Not populated** on the store. | The store row count and the 2.02 coverage. Defined as T164e's evidence, not yet produced. | Only IRRX needs it | complete existing task (T164e), only if IRRX is un-parked |
| 5 | 10-Q/10-K acceptance times (fundamentals) | #1382; claim TP-2 | The SEC acceptance field is shifted by the NY UTC offset. One look-ahead holding was found in B3 trial 3 (2023-02-28). | Stamp errors are a correctness class: repaired, never accepted (ADR 0023 D1) | `restamp-1382a2` was applied on 2026-10-11 (508,539 changes). The a3 store step is disputed (see A3). | STORE for universe CIKs. 18-39 pre-2003 CIKs remain unrepaired. | #1686 (SGML anchors that 404) and #1524 (pre-window gate): open owner calls | Only fundamental signals, all retired or paused | defer |
| 6 | Costs: fills at the open | [Probe 3](2026-10-09-probe3-preopen-fills.md) | Whole-share orders: median +6.2 bp against the official open (n = 4, range −9.3 to +34.7). Fractional DAY orders fill **at the official open** (0 bp). The SIP bar open differs from the official open by −4.4 to +19.8 bp. | D3: `daily` and `week_end` registrations fill at `open` | `execution.fill_price = open` | RES (n = 4; "cannot bound a tail") | Any tail; live fills against paper fills | Rung 1 for every fast idea | reuse |
| 7 | Costs: measured paper slippage | T203/A8b (#1733, **PR #1738 merged** 2026-10-18; follow-up #1741/#1798) | No numbers yet. The Daily book has had no fills (window 4 halted on GEF-B; window 5 starts **2026-10-19**). | Rung 1 input: "per-fill slippage plus half-spread" (ADR 0023) | The `paper report` and `paper status` slippage line, measured against the **SIP bar open** | IMPL + FIX. OPS pending. | **The half-spread term has no source** (no quotes are stored). Fractional fills priced at the official open read about −10 bp against the bar open (P3), so rung 1 can read too low. | B10's rung 1 | Q2 measured: bars cannot give it; owner decision O-1 |
| 8 | Costs: model terms | T210/T210b/T210c (#1792, draft PR #1822 holds failing tests only) | Spread, square-root impact and ADV terms. Default 0, so nothing changes until set. | Spec amendment #1792 | Not built. The base model is still flat 15 bp per side (`costs.per_side_bps`). | DEC | The calibration value | B10's rung 4 uses 30 bp per side (cost doubled). That needs no T210. | defer for B10's rung 4; complete before any survivor's base-level run |
| 9 | Universe: mega-caps missing (cap rank) | S&P answer key, 2026-10-14 (#1682 comment) | 2,232 of 43,325 S&P member-months (5.2 %) are missed for data reasons, about 19 names a month in 2024-26 | ADR 0022 (no vendor). The one data sitting T197 → T186c → a3 → T198 (target 10-24..27). #969 gate (see A3). | Levers built in code: T190/T190b/T196 live at read time. T190d, T191, T192/T192b, T193 and T194 are merged and wait on the T197/T198 releases. | STORE (before). After-release reading is pending. | Whether the post-T198 residue passes the gate | Historical cap universe for B10's rung 4 | reuse the gate; do not duplicate it |
| 10 | Universe: liquidity rank for forward books | T212 (#1743, **PR #1748 merged** 2026-10-18); #1747 | ADR 0023 D4: rank by median raw dollar volume over 12 months. No shares fact is needed. Intersected with Alpaca tradable assets. | D4: new forward books register `dollar_volume`; registered families keep `cap` | `universe.rank_by` (default `cap`, byte-identical) | IMPL + FIX. Quant-auditor PASS. **The spec's evidence line ("the two top-1,000 sets at 2026-10-16", the eight names) was never posted.** | Names per rebalance, overlap with `cap`, and how many DV members lack a shares fact (B10's turnover needs one). The assets intersection is likely skipped everywhere (row 11). | B10's forward book, any new forward book | measured (Q1) |
| 11 | Alpaca tradable-assets snapshots | ADR 0021 (Proposed); T171-T171e built | History starts 2026-10-11 08:09Z, in the owner's scratch recorder | **T171d (import and daily run) is paused** under D5 | `borrow_status` table and reader | IMPL. No evidence the store holds any rows. | If the table is empty, D4's intersection is skipped at every `t` and the flag is not recorded on trials (#1747, open) | The forward book trades names Alpaca may refuse. D2's per-name skip covers that at submit time. | measured (Q1: skipped at all 74 month-ends and on 2026-10-16) |
| 12 | B10's turnover inputs | T165-T165d (all merged: #1387, #1386, #1397, #1402) | Turnover = split-adjusted volume / shares fact known at `t`. A name with no shares pick is excluded and counted. | B10 file drafted (#1358). T165e is the owner's sweep (rung 4). | Built; the paper-side pin is in place | IMPL + FIX | Under `cap`, every member has a shares fact by construction. Under `dollar_volume`, some do not (probe Q1, `dv_no_shares`). | B10 | measured (Q1: 4-8 DV members a month lack a shares fact) |
| 13 | Engine for fast books | T207, T208 (scale), T209a (pure), T217 (keys) merged; T209b, T217c, T217b, T205 open | Without these, a fast backtest is slow, or fills and holds are dishonest (ADR 0023 context) | ADR 0023 build list | Scale budget met (#1799). Hold/band guard until T209b. Delay/placebo guard until T217c. | FIX | — | B10's rung 4 needs **T217c** (one-session delay). Its 20-session-hold variants would need T209b; B10 itself is `month_end` and does not. | complete existing tasks (T217c, T217b) |
| 14 | History depth | [history-extension](2026-10-08-history-extension.md) (#1303); #1597 | Bars from 2016-01-04. The universe is empty before 2019 (no listing rows). | ADR 0022, ADR 0023 D7 (re-open only for a rung-4 survivor that needs pre-2016). T184h paused. | — | STORE | In-sample is 2020-08-31..2023-12-29: about 40 monthly returns, which can kill but not confirm | Power, not feasibility | defer |
| 15 | Spin-offs in total return | T172d (spin-offs 10 → 88, owner 2026-10-16); T211's spin-off half | The backtest does not adjust spin-offs; paper credits them | T211 spin-off half waits on a spec amendment | Records are in the store | STORE (records); the adjustment is UNRES | A few names a year | Small, cited | defer (accepted, cited) |

### A2. Defects relevant to daily and weekly strategies (from #1542, #1551, #1598, #1682, #1382)

| Defect | Class | Evidence | Matters for B10? |
|---|---|---|---|
| Exchange-axis cover listings dropped (IBM, ABBV, ABT, MMM …) | **corrected in code and store** | T181 `cover-listings-1541`, owner-run 10-13/14 (#836) | yes, fixed |
| Stray bars on reused tickers | **corrected in code and store** | `ops-1549b`, `health --check` exit 0 (#1545, #1549). Classes R2, R3 and NINE are out of scope. | low |
| SEC acceptance shift (#1382) | **corrected in code and store** for universe CIKs; residue pre-2003 | a2 (10-11); a3 disputed (A3) | no (B10 reads no filing time beyond the shares fact's `known_at`) |
| Duplicate cash dividends (#1613) | **code only, awaiting store** (T186c `dupdiv-1613`) | PRs #1623, #1629, #1634 | yes (total-return signal), small |
| Note, debenture and coupon rows on primary ids (VZ, PG …) | **code only, awaiting store** (T197) | T191 #1702 | yes (Verizon's listing) |
| Shares-class links, renames and holes, C1/C3, classification (lever F residue, E2, C1/C3, H) | **code only, awaiting store** (T198) | T190d #1727, T192 #1706, T192b #1750, T193 #1701, T194 #1699 | yes: the cap universe |
| SCCO stock dividends booked as cash (#1614) | code fixed (PR #1617). **Store repair not found**: UNRES | #1598 | small, verify only if B10 holds SCCO |
| Holdco CIK change drops BLK (2024-10) and XOM (2026-07) | **deferred under decision** (D1 residue until T187d; T187 paused) | #1615 | historical cap universe: cited. A forward DV book is not affected (D4). |
| Snapshot-static rows (C2), pre-2019 listings | **deferred** (T184h paused, D5) | #1597, #1682 | no: in-sample starts 2020-08 |
| Class-share bars (E1) and FOX's 12 months | **deferred, named residual** (T195 parked; 144 member-months) | #1682 | cited |
| FDIC-filer banks (FRC, SBNY) absent | **deferred, named residual** (TP-4) | [H1 known holes](2026-10-11-h1-known-data-holes.md) | cited (it can only flatter a long book) |
| CBRL special dividend missing from Alpaca | **outstanding vendor limit, accepted** | #1568 | negligible |
| Spin-offs not adjusted in the backtest | **outstanding** (T211 spin-off half) | ADR 0023 context | small |
| Stale statement cache, IFRS issuers, revenue tags | **outside daily strategies' needs** (no running book reads statement facts) | #1551, closed under D1 | no |

Nothing in this table is a look-ahead risk for B10 that the scheduled releases leave open. Accepted residue is not reopened.

### A3. Contradictions found (recorded, not resolved)

| # | Competing statements | Which supersedes | Open factual question |
|---|---|---|---|
| C-1 | **#969's gate.** Three versions: (a) the issue body, 2026-10-05: fix above 0.5 % of in-sample universe-months. (b) The stop-rule comment, 2026-10-16: data-caused misses ≤ 1 % of S&P member-months and ≤ 5 names a month in 2024-26, **or** results moving less than noise, measured once after T197/T198. (c) ADR 0023 D1, 2026-10-18: 0.5 % of universe-months plus a correctness class. D1 closed #969. | D1 is the newest record and closed the issue. #836's handover and this brief still list (b)'s measurement "after T198". | Does the owner still want (b)'s one measurement after T198? This report assumes yes, as the brief says, and proposes nothing that duplicates it. |
| C-2 | **The `restamp-1382a3` store step.** T166c (amended 2026-10-18) and STATUS.md say it runs in the 10-24..27 sitting. #1553 and #1582 record it as done on 2026-10-12 (4,978 changes; the `after` record at 2026-10-12T20:42Z). | Unclear | The owner's `data/releases.toml` decides it (not read here). |
| C-3 | **Stale plan boxes.** T203 (PR #1738 merged), T190d (#1727), T191 (#1702), T192 (#1706), T193 (#1701) and T194 (#1699) are merged but unticked. T164e's code is merged and its box is rightly open (the evidence step is outstanding). | The commits | None: a fold should tick them |
| C-4 | **D4 against D5.** D4 says a forward DV universe is "intersected with Alpaca's tradable assets at `t`". D5 pauses T171d, the only task that fills `borrow_status`. | Both stand | Answered by Q1: the intersection is skipped at every measured session, so `borrow_status` holds no usable rows. Whether to un-pause T171d is open. |
| C-5 | **The H1 known-holes note** (2026-10-11) says IBM, ABBV and others are missing "until `cover-listings-1541`". T181 has since run. | T181's run (10-13/14) | None (the note is frozen; it is cited as of its date) |
| C-6 | **B10's rungs 2 and 3.** T165e says they "run during the wait". No note on any issue records them. | — | Not run, as far as the repo shows |

---

## B. Strategy readiness matrix

The decision timestamp is always the session close at `t`. Fills are at the next open (D3) for `daily` and `week_end`. Registered `month_end` files keep what they froze.

| Strategy | Minimum data | Existing data and interfaces | Research verdict | Validation | **Verdict** | Blocker | Existing task or decision |
|---|---|---|---|---|---|---|---|
| **B10 short-term momentum** (last month's winners in the top turnover quintile of the top 1,000, about 40 names, equal weight, `month_end`, one-month hold; in-sample 2020-08-31..2023-12-29; about 90 %/mo one-sided turnover; at least 1 month of bars plus a shares fact known at `t`, split-adjusted) | Bars with volume, splits and dividends, shares fact, cap universe | All held. `momentum` family with `strategy.turnover_top_fraction` (T165-T165d). `StoreProvider.turnover_inputs`. Paper dispatch pinned. | SH-9 INSUFFICIENT (gross 0.42 %/mo long-short, to 2018; long leg unreported). Prior net ≈ 0 to −1 pp/yr against SPY at 15 bp. The cost margin decides it. | FIX; never run on the store | **READY WITH RESTRICTIONS** for rungs 2-3 now. **REQUIRES EXISTING TASK** for rungs 1 and 4. Rung 1's half-spread needs **an owner or orchestrator decision** (O-1): Q2 showed daily bars cannot measure it. | Rung 1 needs about 10 Daily-book fill sessions (from 2026-10-19, so about 10-30). Rung 4 needs T217c (delay) and the sweep file (class B), on the post-T198 store. | T165e, T217c, T217b, T203, ADR 0023 ladder; O-1 |
| B10 forward book (tracking window, never an exam: momentum holdout spent) | As above, plus today's universe | `dollar_volume` rank (T212) if registered under D4; `cap` otherwise | — | IMPL | **READY WITH RESTRICTIONS** (only if B10 survives rung 4) | Which `rank_by` the book registers (O-2). Under DV, Q1 measured 4-8 members a month with no shares fact (excluded from turnover, counted) and no assets intersection (D2's submit-time skip covers it). | T161 item 4; Q1 (measured) |
| Daily momentum shakedown (operations book, 12-1, top 20 %, `daily`) | Bars | Running | Machine test (−6.13 pp/yr in sample; not a bet) | OPS from 2026-10-19 | **READY** (as a machine test only) | — | T162 shakedown |
| Daily/weekly plain reversal | Bars | No reversal sort and no sub-month formation window (`formation_months` is a whole number of months); would need a new root family | SH-1, SH-2 NOT SUPPORTED; QI-17 parked. Large-cap price pressure has a half-life of about 0.54 days, unreachable at next-open fills (SH-4's note). | — | **REJECTED** (economic, not data) | — | Backlog "Parked" |
| Liquidity- or turnover-conditioned signals | Bars, volume, shares | B10's screen; T212's DV rank; rule 5's liquidity floor | SH-9 (screen); SH-1 (low-turnover names reverse) | FIX | **Covered by B10** (its no-screen control tests the sign). No separate strategy. | — | T165e (two-variant sweep) |
| IRRX (industry-relative, announcement-adjusted reversal, monthly, low-volatility half) | Bars, SIC (held), 8-K 2.02 dates | `filing_events` code (T164b-e), not populated. Needs a new root family and a store-side long leg. | SH-4 MIXED gross. Net 0.19 %/mo (t 1.41, to 2013). Rung 2 INSUFFICIENT (no public long leg). | IMPL + FIX for the data path | **DEFERRED** (parked behind T198 by the owner) | Owner decision; then T164e's real-store run and an engine build of size M or more | [desk note §3](2026-10-18-b9-kill-b3-retirement.md), T164e |
| B9 EAR drift (8-K 2.02 reaction, 20-session hold, daily) | Bars, 8-K 2.02 times | As IRRX; hold mode T209b | Killed at rungs 1-2 (economic) | DEC | **REJECTED** | — (not data-blocked: TP-1 says the dates exist) | #1765 |
| Fundamental or disclosure signals (B3, B3b, B4; B5-B8) | Statement facts, filing text, Form 4 | Facts STORE (99.3 % coverage, #1542). No text and no Form 4. | B3 retired (−3.28 pp/yr). B3b and B4 dropped. B5-B8 INSUFFICIENT or paused. | STORE for facts | **REJECTED** (B3 family). **DEFERRED** (B5-B8: new data, off the fast-strategy track, D5). | New data for B5-B8 only; not justified now | Backlog; D5 |
| Hedged or long-short books (B12 decomposition, overlays, pairs) | As above, plus T-bills (T170 built; DTB3 in the sitting) | B11 done; T211 metrics half merged; signed positions not built; shorting forbidden | NOT SUPPORTED or INSUFFICIENT; LS-13: a hedge adds no edge | RES | **DEFERRED** | No long leg survives to hedge. A charter or ADR change is needed for shorts. | beyond-long-only §"What the system needs"; ADR 0015 |

---

## C. Targeted verification (owner-run, read-only)

Only questions that are material and that the #969 measurement will **not** answer are listed here.

**What the #969 measurement will answer (not repeated here):**
- the cap universe's data-caused S&P misses in 2024-26 after T197 and T198 (baseline: 5.2 %, about 19 names a month);
- whether the H1 and Daily results move by more than noise.

**What it will not answer:**
- the dollar-volume universe;
- shares-fact gaps under that universe;
- the half-spread;
- 8-K coverage, which T164e's own evidence answers if IRRX is un-parked;
- B10's published long leg, which is rung 2 arithmetic and not a store question.

Both scripts:
- live in `~/tradepartner-probes/daily-readiness/`, outside the repo;
- open the store with DuckDB `read_only=True`, taking its path from argv (never defaulted);
- write nothing;
- import the repo's own code, so run them from the main checkout with `uv run`;
- read `universe.*` from the owner's live settings;
- were tested on a fixture store built in a temp dir from `tests/fixtures/universe/` (never `data/tradepartner.duckdb`), against repo commit `0eeac1b7`.

| Id | Question, and the decision it moves | Script (sha256 prefix) | Exact command | Window, denominator | Limitations | Result |
|---|---|---|---|---|---|---|
| **Q1** | Under `rank_by = dollar_volume`: how many names per rebalance; overlap with `cap`; which names only DV holds; how many DV members have **no usable shares fact** (no B10 turnover); was the assets intersection skipped? It also fills T212's unposted spec evidence line (2026-10-16). Moves O-2 and the B10 forward book's design. | `universe_census.py` (`fade08ac306fe26c`) | `uv run python ~/tradepartner-probes/daily-readiness/universe_census.py <store> 2020-08-01 2026-09-30 month_end --csv ~/tradepartner-probes/daily-readiness/q1-month-end.csv` and `uv run python ~/tradepartner-probes/daily-readiness/universe_census.py <store> 2026-10-16 2026-10-16 daily` | Month-end sessions 2020-08..2026-09 (about 74) plus 2026-10-16. Denominator: members per session under each rule. | Uses `universe_as_of` exactly as the engine does, on today's store vintage: pre-T198 if run before the sitting, so it should be re-run after. Runtime not measured on the real store; two universe builds per session may take tens of minutes. `dv_no_shares` uses `shares_as_of`'s pick, the same one turnover uses. | **Measured 2026-10-18, code 3b261961** (the orchestrator ran it, read-only, on the real store, about 25 s per session). **2026-10-16:** n_cap 1007, n_dv 1008, overlap 892, DV-only 116, cap-only 115, DV members with no shares fact 7, assets intersection **skipped**. DV-only sample: ONDS CIFR QBTS AXTI MARA RGTI SMR IBKR. **74 month-ends, 2020-08-31..2026-09-30:** n_cap median 1008 (range 1006-1010); n_dv median 1013 (1009-1015); overlap median 879 (859-896); DV-only median 133.5 (114-154); cap-only median 129.5 (111-149); no shares fact median 7 (4-8), above 0 in all 74; intersection **skipped in all 74** (confirms C-4). CSV `~/tradepartner-probes/daily-readiness/q1-month-end.csv` (local). **Reading:** the two rules share about 87 % of names. About 13 % differ every month, and the DV-only names lean to high-volume speculative names. The shares-fact gap under DV is small (about 0.7 % of members). |
| **Q2** | What half-spread belongs in B10's rung-1 sum (median slippage + half-spread ≤ 12 bp per side)? With no quotes stored, an Abdi-Ranaldo close-high-low estimate from bars gives a first number for the top 200 by dollar volume (B10's screened pool size) and by liquidity quintile. Moves rung 1, together with O-1. | `half_spread.py` (`a4870a0be235f4a2`) | `uv run python ~/tradepartner-probes/daily-readiness/half_spread.py <store> 2026-10-16 --window 63` and `uv run python ~/tradepartner-probes/daily-readiness/half_spread.py <store> 2023-12-29 --window 63 --rank-by cap` | 63 sessions ending at each date. Denominator: universe members with at least 10 usable bar pairs. | An estimate, not a quote measurement. Noisy and biased upward for liquid large caps, and floored at 0 when negative (the share at 0 is printed). Raw bars; a pair across a split or a gap above 0.2 log is dropped. Read it as an upper-side bound, never a calibrated cost. | **Measured 2026-10-18, code 3b261961** (the orchestrator ran it, read-only, on the real store). **2026-10-16, dollar_volume, 63 sessions:** 1008 members, all estimated. All names: median 7.6 bp, p75 39.3, p90 69.4, 48 % floored at 0. Top 200 by DV: median 15.6, p75 44.3, p90 82.6, 44 % at 0. Quintile 1..5 medians: 15.9, 2.2, 0.0, 10.6, 10.9 bp (p90 82.6, 73.2, 65.0, 68.2, 61.8; share at 0 44/50/53/47/46 %). **2023-12-29, cap:** 1009 members; every quintile median 0.0 bp, 60-70 % at 0, p90 31-48. **Reading: unusable for rung 1.** Half the names floor to 0, and the estimate is not monotone in liquidity (the most liquid quintile reads highest). Daily bars cannot supply the half-spread. O-1 stays open. |

Both scripts printed sane counts on the fixture store, with expected shapes such as `dv_skipped_assets = 1` before the fixture's first assets snapshot. Fixture numbers are not evidence and are not reported.

---

## D. Decision memo

**What we can research now.**
- **B10 rungs 2 and 3** (read-only arithmetic, a note on its issue). T165e already says they run during the wait, and nothing records them (C-6). *Existing conclusion, not yet done.*
- Probes Q1 and Q2 have run (section C, measured 2026-10-18). *New.*
- The Daily book, as a machine test from 2026-10-19.

**What progresses, with limits.**
- **B10 rung 1:** it needs about 10 fill sessions (about 2026-10-30) and a defined half-spread.
- **B10 rung 4:** it needs T217c and the sweep file (class B), on the post-T198 store.
- **The limits:**
  - The in-sample window is 40 months: it can kill, not confirm.
  - The holdout is spent, so B10 can only ever get a tracking window.
  - The prior is about zero net. *Existing conclusion.*

**What genuinely blocks priority strategies.** No data gap does.
- B10 waits on fills, one engine task and one sitting.
- IRRX waits on an owner un-park, then T164e's real-store run and an engine build. Its economic case is the weakest net. *Existing conclusion.*

**Which existing tasks come next, in order.**
1. T217c, then T217b.
2. The one data sitting: T197 → T186c → a3 (settle C-2 first) → T198, with the #969 measurement if the owner keeps it (C-1).
3. T165e: B10's rung 4 and rung 5 placebo, after rung 1 passes.
4. T210, T210b and T210c before any survivor's base-level run.
5. A fold that ticks the stale boxes (C-3).

**Gaps that stay deferred.**
- T164e's store run: IRRX only.
- T171d's asset snapshots: Q1 confirms the intersection is skipped at every month-end (C-4). D2's submit-time skip covers forward books, so this stays deferred unless a forward book's skips grow.
- T184h (pre-2019), T187 (chains), T195 (E1), the spin-off adjustment, and #1686 and #1524 (fundamentals only).

**New data source or task?** None is justified. No proposed new task meets the four-field rule (existing-work search · why existing work is insufficient · minimum scope · decision unlocked). Every gap found is covered by an existing task or by an owner decision.

**Owner decisions surfaced** (both *new*):
- **O-1:** what source supplies rung 1's half-spread. **Q2 rules out daily bars**: the close-high-low estimate floors half the names to 0 and is not monotone in liquidity. The choice is now between a fixed conservative assumption (HO-14's floor: 5-10 bp plus half the spread) and another source, such as the NBBO at submit read from the broker. The orchestrator decides when rung 1 comes due (about 10-30). Note: the slippage line measures against the bar open, which reads fractional fills about 10 bp low (P3). *New.*
- **O-2:** whether a B10 forward book registers `cap` (as its family) or `dollar_volume` (as D4 says for new forward books). Under DV, turnover needs shares facts that some DV members lack (Q1).

**Smallest next sequence.**
1. Now: B10 rungs 2-3 notes (Q1 and Q2 are done).
2. About 10-30: rung 1 from `paper report`'s slippage, plus the O-1 half-spread.
3. After T198 and T217c: rung 4 (30 bp, one-session delay, open fills), then rung 5.
4. If B10 dies, the ladder is empty. Only a new idea through rung 1-2 arithmetic comes next, not data work. ADR 0023 says to revisit the ladder if B10, B9 and IRRX all die at rungs 1-2.

**Stop doing:**
- data work for B9, IRRX or fundamentals;
- new identity censuses or health rules (D5);
- daily or weekly reversal designs;
- long-short builds before a long leg survives;
- reading any universe or fixture result as store evidence.

---

## Sources

- Decisions: [ADR 0022](../decisions/0022-no-vendor-security-master.md), [ADR 0023](../decisions/0023-break-first-fast-books-paper-exam.md), [ADR 0021](../decisions/0021-alpaca-assets-borrow-status-record.md).
- Research: [short-horizon](2026-10-09-short-horizon-candidates.md), [beyond long-only](2026-10-10-beyond-long-only.md), [B11](2026-10-10-b11-beta-alpha-split.md), [Probe 3](2026-10-09-probe3-preopen-fills.md), [H1 holes](2026-10-11-h1-known-data-holes.md), [history extension](2026-10-08-history-extension.md), [B9/B3 note](2026-10-18-b9-kill-b3-retirement.md), [IRRX long leg](2026-10-18-irrx-long-leg.md), [IRRX retry](2026-10-18-irrx-rung2-retry.md), [backlog](hypothesis-backlog.md), [claims](claims.toml) (SH-1, SH-2, SH-4, SH-6, SH-9, LS-13, TP-1, TP-2, TP-4, QI-17, HO-14).
- Hypotheses: [B10](../hypotheses/b10-short-term-momentum.md), [Daily shakedown](../hypotheses/momentum-daily-shakedown.md).
- Plans: [data-foundation](../plans/data-foundation.md) (T164-T164e, T171d, T186c, T197, T198, T212, T214), [backtest](../plans/backtest.md) (T165-T165e, T207-T211, T217-T217c), [paper-trading](../plans/paper-trading.md) (T161, T162, T203, T205).
- Issues and PRs: #836 (orchestrator log: trial results, owner reads), #969 (comment 6210369406), #1382, #1542, #1551, #1598, #1682 (answer key comment 6178707959), #1743, PR #1748, #1747, PR #1738, #1792, PR #1822.
- Code: `src/tradepartner/universe.py` (`_dollar_volume_universe`, `shares_as_of`), `backtest/signals.py` (`turnover_screen`), `backtest/strategies.py`, `config.py` (`StrategyConfig`, `rank_by`, `filing_events_enabled`).
- Owner machine, not read for this report: `data/releases.toml` (decides C-2); `~/tradepartner-probes/research-library/` (the coverage audit and the answer-key tables, cited through #836).
