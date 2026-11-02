# Design: an earnings-announcement event family on 8-K Item 2.02 acceptance times

**Issue:** #2892  ·  **Date:** 2026-11-02  ·  **Kind:** design note for a candidate under the owner's second strategy-search limit (4 candidates or 2026-12-15; [retro #2835](../retros/2026-11-01-strategy-search-retro.md); tally 1 of 4 after the composite FAIL, #2888). The brief numbers it candidate 3 of 4; the backlog's second-limit paragraph names it as the first of the two the owner listed. The tally is the orchestrator's to settle.  ·  **Agent:** team reseventprep, claude-opus-5-5  ·  **Reads:** code and docs only. No store was opened, no return of any kind was read, and nothing was registered.

## Summary

- **The idea is B9 again, with four changes.** The changes are an event-time entry one session after the reaction, a 60-session hold instead of 20, matched announcer peers instead of the market, and a short side. B9 was killed on a published monthly yardstick, not on our own data. Section 1 says which changes could matter and which only shift the cost arithmetic.
- **The binding risk is cost, and on graded evidence it fails.** At the measured +24.2 bp per side (rounded up to 25), a long-versus-peers event pays 50 bp round trip. A short pays 57 bp: 50 bp plus 7.1 bp of borrow (30 bp/yr over 60/252 of a year). The published size-screened yardstick (SH-6) implies about 20 to 35 bp of gross spread per 60-session event on the long side and about 51 bp on the short side. Stage 4a item (d) therefore **fails for both theses** at today's cost. At the liquid-name, open-sent cost (+10.6 bp, interval up to +15.4) it is met for the short and borderline for the long.
- **Power is thin as well.** The kill t is taken on the net spread. For a true net effect at the +30 bp floor to reach t = 1 takes about 2,500 effective events per thesis. That is about 10,000 traded events, against the roughly 6,000 per thesis the window holds.
- **Nothing in the store can answer it yet.** `filing_events` has **0 rows** (orchestrator fact check, store copy 2026-11-02, schema 41). Its ingest code is merged (T164e code, PR #1459), but `edgar.filing_events_enabled` defaults to `false` and the real-store run that flips it was never made. T324's seed fills `filing_index`, which has no item codes. **The run that makes Item 2.02 queryable is T164e's real-store run (owner, size S).**
- **Recommendation.** Run the counts-only feasibility check (section 4) right after T164e's run; it reads no returns. Freeze and run the return read only if the owner accepts 4a(d) as written here, or once the buy-timing cost work measures 15 bp per side or less on these names. Otherwise record the candidate as `deferred: 4a(d) cost` in the ladder ledger, at the price of a ledger row and not a candidate slot.

## 1. What is different from B9, C1 and C1b, and why it might survive cost

### What B9 was and how it died

B9 sorted on the [−1, +2] market-adjusted return around the 2.02 acceptance, held the top quintile for 20 sessions and rebalanced daily, for a turnover of 100 to 200 % a month. It was killed at rungs 1 and 2 on the Chen-Zimmermann (CZ) `AnnouncementReturn` size-screened long leg: +0.07 %/month, t 0.94, 2010-2024, break-even 1.75 to 3.5 bp per side ([desk note](2026-10-18-b9-kill-b3-retirement.md)). It never touched our store.

### The four changes

| # | Change | Effect | Graded? |
|---|---|---|---|
| 1 | **Event-time entry.** Day 0 (`s0`) is the first session whose close follows the acceptance. The reaction is close(`s0`−1) to close(`s0`). Entry is the open of `s0`+1. | CZ forms its portfolio at month end, so its yardstick enters 0 to about 21 sessions after the announcement and **leaves out the first part of the drift**. Our entry sits about one session after the reaction closes. If drift is front-loaded, the yardstick understates our read. | **UNGRADED.** No claim in `claims.toml` measures the drift in the first 21 sessions on its own. |
| 2 | **A 60-session hold** (about one quarter, ending before the next release). | It cuts turnover by 3 against B9's 20 sessions: about 4.2 round trips a year per position. Each event's gross spread has to clear 50 bp once, not three times. | Arithmetic. Whether drift keeps its pace through 60 sessions is not graded. CZ holds for one month. |
| 3 | **Matched peers, not the market.** The comparison is the event's return minus the mean of announcers of the same size tercile in the middle reaction band, entered within 5 sessions (C1b's peer design). | It removes the size and beta effects that misled C1 run 2. It removes the announcement-season effect too, because every peer has just announced. It does not move the cost. | Method (stage 4a item (e)). |
| 4 | **A short side** (bottom-quintile reaction, with borrow). | ADR 0024 removed long-only. SH-6's scope records the short leg as the stronger one in the value-weighted file (about 0.49 %/month gross, in conflict with a CRSP DGTW claim). | MIXED, with a recorded conflict. |

Exact acceptance times also fix a data trap. About 50 % of 2.02 8-Ks are accepted between 16:00 and 17:30 New York and carry that session's `filed` date (TP-1). A `filed`-keyed study would put day 0 a session early and fold the announcement gap into the "pre-event" window. That fixes a bias; it is not a new source of edge.

### What is genuinely new, and what is not

- **Not new:** the mechanism (the market under-reacts to earnings news, so the reaction predicts drift). It is B9's, and SH-6 is its evidence. **This is a second look at a killed idea and must be disclosed as one.** B9 had 2 looks in the ladder ledger (size-screened, then value-weighted, the second reported and not used). A registration must list them.
- **Not out of sample against B9's yardstick.** The store window (2016-07 to 2023-12) lies inside the CZ 2010-2024 months already read for B9 (ladder rule L4). A result here is a different design on months that have been seen, not fresh data.
- **New:** a test on our own store in event time, with matched peers, a short side and a hold that amortises cost. B9's kill came from a monthly published series that leaves out the early drift and charges for 20-session turnover. The B9 note's rung arithmetic does not apply to a 60-session hold. Whether the read is worth a candidate slot depends only on change 1's size, which is unknown.
- **C1 and C1b** keyed on the 10-Q/10-K *filing lag* (an early or late filer against normal-lag peers), not on the market's reaction. Their event is a different filing, and the signal is timing, not reaction sign. The windows overlap (C1b ran 2015-04 to 2023-12), so the market paths are shared, but the event lists and the signal are not. C1b's peer design is reused here as a method only.

### Is a 10-Q/10-K acceptance from `filing_index` an honest proxy?

**No. It is a different event**, and SH-6's own note says so: "a 10-Q-keyed version is a different, untested event (ER-6)". From T164's count (TP-1, 2020Q3 to 2023Q4):
- The 10-Q/10-K follows the latest 2.02 8-K by a median 1.0 day, p75 7.4 days, p90 15.9, and about a week in Q1. A reaction window keyed on the periodic filing would usually start **after** the price move it is meant to measure.
- 4.9 % of periodic filings have no 2.02 8-K in the 60 days before them, so the two event lists differ too.

The proxy would test "the return after the 10-Q", not "the reaction to the earnings release". It is not used, not even as a fallback (B9-6, owner answer on #1358: 8-K only, no fallback).

## 2. The exact data query and the point-in-time rules

### Tables

| Need | Table / read | Columns |
|---|---|---|
| The event (item codes) | `filing_events` via `store.asof.filing_events_as_of(conn, t, forms=["8-K"], items=["2.02"])` | `security_id` (joined through `securities_as_of(t)` on `cik`), `cik`, `accession`, `form`, `items`, `accepted_at`, `known_at` (= `accepted_at` by the table's CHECK) |
| A cross-check of the acceptance | `filing_index` via `store.filing_index.first_known(conn, t, cik=…, form="8-K")` | `accepted_at` of the first-known revision of `(cik, accession)` |
| Universe membership (liquidity, and the names the cost was measured on) | `universe.universe_as_of(conn, t)` | members at `t` |
| Reaction, entry, exit, beta | the event-study reader's bars (`EventStudyReader.bars`, adjusted and raw) | open, close, volume |
| Market cap for size buckets | `EventStudyReader.shares` (`shares_outstanding`, company-level, `facts_read = "event_known_at"`, at most 400 days stale) | value, as-of date |

Item matching is whole-code. `filing_events_as_of` splits `items` on commas and matches `2.02` as a code (`12.02` never matches). The store does not parse codes.

### Point-in-time rules (the event builder implements these. The runner already implements the entry and peer rules)

1. **Known-at = acceptance.** `known_at` of the filing is `filing_events.accepted_at`, tz-aware UTC, never the index's `filed` date. Read `filing_events_as_of` at the instant of the reaction close (rule 3), so only filings accepted by then are visible.
2. **Day 0 (`s0`).** Let `D` be the New York date of `accepted_at`. If `D` is an XNYS session and `accepted_at < session_close(D)`, then `s0 = D`. This covers pre-open (about 45 % of events) and intraday (about 2.5 %) acceptances. Otherwise `s0 = next_session(D)`. That covers after-close acceptances (about 50 %, including the 16:00 to 17:30 `filed`-date trap) and weekend or holiday ones. `session_close` handles half days: an 8-K accepted at 13:30 on a 13:00-close day belongs to the next session.
3. **Reaction and signal time.** The reaction is `R = [adj close(s0) / adj close(previous_session(s0)) − 1] − [the same for SPY]` (market-adjusted, not beta-adjusted, so it needs no estimate). The event's `known_at` handed to the runner is **τ = `session_close(s0)`**. The runner's `entry = "next_open_after_instant"` then makes the entry the open of `next_session(s0)`. The signal is formed at a close that has happened and traded at the next open, the paper books' pre-open cadence.
4. **Groups from causal breakpoints.** The 20th and 80th percentiles of `R` over all eligible events whose τ falls in the **63 sessions before `s0`** (strictly before τ). With fewer than 400 events in that window the event is unlabeled (`group = ""`, counted as `n_no_label`). Groups: `up` (R above p80), `down` (R below p20), `mid` (the rest, the peer group).
5. **Universe.** The event's security must be a member of `universe_as_of(τ)`. This is the liquidity screen, and it keeps the read on the names whose paper cost was measured. The runner adds price at least $5 and median 20-session dollar volume at least $10M on raw bars before entry.
6. **Forms and amendments.** Only `form = "8-K"`. An `8-K/A` is never an event: it amends a release, and T164's count found at most one issuer a quarter with a 2.02 `8-K/A`. Its count is reported.
7. **One event per release.** Within a CIK, a 2.02 8-K accepted within 20 sessions after an earlier event of the same CIK is not an event. It is counted (`n_dup_20s`) and usually a corrected or second release. The first one stands, so a pre-announcement followed by the release keeps the pre-announcement. That is declared, not tuned. The 20 sessions run from the last **kept** event, so a chain of three filings 15 sessions apart keeps the first and the third. A CIK with several listed classes yields one event, on the class with the highest median 20-session dollar volume before `s0`. `issuer_key = cik`.
8. **Acceptance cross-check.** An event whose `filing_events.accepted_at` differs from `filing_index`'s first-known `accepted_at` for the same `(cik, accession)` is dropped and counted (`n_stamp_mismatch`). Both come from the same stamps, so the expected count is 0. A non-zero count is a stamps-clock signal (#1382) and is read before anything else.
9. **Window (ADR 0016).**
   - Bars start 2016-01-04. The first entry is **2016-07-01**: 63 sessions of breakpoint history plus a 120-session beta with at least 60 pairs.
   - The last exit is the development boundary **2023-12-29**. The runner drops an event whose 60th session falls after it (`horizon`).
   - Nothing after 2023-12-29 is read. ADR 0016 point 8's 2024-01 to 2026-09 exam is only for a frozen finalist.
   - **Acceptance is not dissemination.** EDGAR disseminates a filing accepted after 17:30 New York on its next business day. When that day is an XNYS session but not an EDGAR business day (Columbus Day, Veterans Day), `R` can close before the filing is public. The entry is still after dissemination, so a label can be wrong but no trade sees the future. These events are **dropped and counted** (`n_edgar_holiday`).
   - T324's history back to about 1993 does **not** lengthen this window, because the store has no prices before 2016 (the retro: "do not buy older prices yet"). Item 2.02 also starts only on 2004-08-23 (open question 13). Before that, results were Item 12.
10. **Delistings.** The runner carries the last close through the scheduled horizon with no delisting return. Stage 4a item (f) counts how often that happens (section 4).

### Event-builder query, in outline (read-only, on a store copy)

```text
for each session s from 2016-03-01 to 2023-12-29:            # breakpoint warm-up before 2016-07-01
  t = session_close(s)
  fe = filing_events_as_of(conn, t, forms=["8-K"], items=["2.02"])
       restricted to rows with s0(accepted_at) == s                       # rule 2
  check each row against filing_index.first_known(conn, t, cik=row.cik, form="8-K")   # rule 8
  keep rows whose security_id is in universe_as_of(conn, t)               # rule 5
  apply rule 7 (dedupe within 20 sessions; one class per CIK)
  emit event_id = accession, security_id, issuer_key = cik, known_at = t, stratum = "8-K",
       accepted_at, s0, R (rule 3), group (rule 4)
```

The builder writes one CSV (`e1-earnings-reaction-events`, event column `known_at`). The owner registers it with `dataset register`, and `experiment study` runs it.

## 3. DRAFT kill line (not posted, not frozen)

> **DRAFT.** Nothing below is frozen. The freeze is a new comment on the candidate's issue, posted before any return in the dataset or the run is read (the #2828 and #2888 discipline). Building the dataset computes `R` (a pre-entry return) to assign groups. The freeze must therefore be posted **before** the builder's `frame` step runs, and the builder must print no group statistic other than counts.

### Pins (filled at the freeze)
- The store copy's file name and SHA-256. The data release id. The schema version.
- The commit of `src/tradepartner/research/event_study/`.
- The builder script `~/tradepartner-probes/earn202/build.py` and its SHA-256, run once to `events.csv`. The `experiment study` run id. No seed (nothing random).

### Design (runner `[study]` table)
`horizons = [20, 60]`, where 20 is B9's horizon and reported only. `groups = ["up", "down", "mid"]`, `peer_group = "mid"`, `entry = "next_open_after_instant"`, `facts_read = "event_known_at"`, `matching = "entry_known"`, `bucket_by = ["quarter", "stratum"]`, `peer_by = ["stratum"]`, `n_buckets = 3`, `min_bucket_events = 30`, `price_floor = 5.0`, `volume_floor = 10000000.0`, `volume_lookback = 20`, `min_volume_bars = 10`, `shares_stale_days = 400`, `shares_fact = "shares_outstanding"`, `benchmark_security = "SPY"` (a beta regressor only, never a benchmark), `peer_window = 5`, `min_peers = 5`, `near_k = 10`, `beta_lookback = 120`, `beta_min_bars = 60`, `cost_bps = [10.6, 15.0, 25.0]`, `borrow_bps = [30.0, 300.0]`, `z = 1.645` (one-sided 95 %), `cluster_by = ["month", "quarter"]`, `configurations = 4` (the kill lines below).

The `[[study.kill]]` lines decide the verdict, so every field is pinned:

| Field | Line `L` | Line `S` |
|---|---|---|
| `side`, `group`, `horizon` | `long`, `up`, 60 | `short`, `down`, 60 |
| `readings` | `["raw", "beta_adjusted", "size_tight", "size_tight_beta_adjusted"]` | the same |
| `metric`, `floor` | `net_bp`, 30.0 | `net_bp`, 30.0 |
| `t_metric`, `t`, `t_floor` | `net_bp`, `min`, 1.0 | `net_bp`, `min`, 1.0 |
| `cost_bps`, `borrow_bps` | 25.0, 0.0 | 25.0, 30.0 |
| `entry_start` (build item 4) | 2020-01-02 on a second line `L2020` (raw reading only) | the same, line `S2020` |

The runner needs `configurations` to be at least the number of kill lines, so it is 4. The family's N records 4 configurations: two theses, each read on the full window and on 2020+.

### Cells (eight decision cells)

| Thesis | Group, side | Readings (each a cell) | Windows | Cost in the line |
|---|---|---|---|---|
| **L** | `up`, long, 60 sessions | raw, beta-adjusted, size-tight, size-tight beta-adjusted | full: entries 2016-07-01 to the last entry whose exit is on or before 2023-12-29 | 25 bp per side (2 sides on the treated leg, the matched leg costless, as the runner does) |
| **S** | `down`, short, 60 sessions | the same four | the same | 25 bp per side plus 30 bp/yr borrow prorated over 60/252 |

Each thesis also has a **sub-window cell** on the raw reading: entries from 2020-01-02 to the end of the window, with the same floors. The CZ yardstick was insignificant in 2019-2023, so a pass must not come from 2016-2019 alone. The cell is a registered kill line (`L2020`, `S2020`), so the registry's `kill_<name>` flags and this verdict cannot disagree. The runner has no per-line window today, which is why build item 4 comes before the freeze.

### Floors
- A cell passes only if the **mean net spread is at least +30 bp per event** **and** its **t is at least 1.0**.
  - The t is the runner's overlap-calibrated t of the per-event net spread: `overlap_result_interval` on `entry_index`, a flat Hansen-Hodrick kernel with lag `horizon − 1 + 2 × peer_window` = 69 sessions. In the per-line kill path every `t_*` key holds this one t, so `t = "min"` selects it. The month- and quarter-clustered t's are reported only.
  - +30 bp per 60-session event is about 0.10 %/month over the hold, after costs. That is the #2828/#2888 floor carried into event time, with costs **inside** the floor rather than reported beside it, because cost is what killed B9.
  - In gross terms, L needs a spread of at least 80 bp per event, and S at least about 87 bp.
- Equality passes. Unrounded values are compared. A reading that cannot be computed (no events, a non-finite mean or t) is a **kill**, which is what `evaluate_kill` records. INSUFFICIENT is not used.

### Combine and verdict rules
- **Combine:** none across events beyond the runner's equal-weight mean. No vol weighting, no reweighting by `R`.
- **A thesis passes** only if all four readings pass **and** its 2020+ sub-window cell passes.
- **Verdict:**
  - **PASS** if at least one thesis passes. Both theses are declared now, so the result names which one passed, and a pass sends only that thesis on to rung 3 (capacity) and rung 4.
  - **FAIL** if both theses fail a computable cell.
  - **UNDERPOWERED** (decides nothing, retires nothing) if a thesis has fewer than **2,500 traded events** (section 4: the count at which a true net effect of +30 bp would reach t = 1 with a 1,500 bp sd on raw events, before clustering). Between 2,500 and 10,000, a kill is labelled `killed_low_power`: it rules out net effects well above the floor, not one at it. The registry flag stays what the runner computes, and the label goes in the ledger and the verdict comment.
  - Two theses give two chances. That is declared, it counts as 2 configurations in the family's N, and the candidate counts once under the limit.
- **Reported only, never changing the verdict:**
  - net at 10.6 and 15 bp per side; borrow at 300 bp/yr;
  - the 20-session horizon (B9's), for comparison with the dead idea;
  - the SPY-adjusted and raw event returns;
  - the share of events by acceptance time of day (pre-open, intraday, after close);
  - `n_dup_20s`, `n_stamp_mismatch` and the 8-K/A count;
  - the delisted-exit count;
  - each year's mean spread.
- **Cost at the freeze.** The DRAFT uses 25 bp per side (the measured +24.2, rounded up, [fill-cost note](2026-10-30-paper-fill-cost-arrival.md)). The freeze may replace it only with a paper figure **measured and published before the freeze** on the buy-timing amendment's fills, taking the upper end of its 95 % interval, and never below 10 bp. That choice must be written in the freeze comment.

### Outcome
- PASS: the passing thesis gets a rung-3 capacity note and a store backtest design. A PASS is not net alpha.
- FAIL or UNDERPOWERED: the design is retired under this slug. A later look is a new registration that discloses this one and B9's two looks.
- Either way it counts as one candidate under the second limit.

## 4. Feasibility count plan (stage 4a; counts only, no returns)

### What to run
Run on a store copy after T164e's real-store run, with a separate `counts` subcommand of the builder that reads no bar after `s0−1`'s close and computes no `R`. All of it is inside the development boundary. The probe runs outside the repo, so no code guards the boundary for it. The `counts` subcommand therefore asserts that no session after 2023-12-29 and no bar after `s0−1` is read, and it fails if either happens.

| Item | Count | Expectation (from TP-1 and C1b) | Gate |
|---|---|---|---|
| (a) causal timestamp | Share of 2.02 8-Ks of universe CIKs with `filing_events` rows. `n_stamp_mismatch` against `filing_index`. The run row's `filing_events_items_missing` and `filing_events_clock_suspect`. | Coverage within 1 pp of TP-1 per quarter, 2020Q3 to 2023Q4 (T164e's own pass rule). Mismatch 0. | Mismatch 0 and clock-suspect 0 for the window's CIKs, or the read waits |
| (b) events per year | Eligible events per calendar year, 2016-07 to 2023-10, after rules 5 to 7. The split by time of day. `n_dup_20s`. 8-K/A count. | About 1,000 to 1,125 a quarter in the universe, so **about 4,000 to 4,500 a year and about 30,000 in the window**. The tails take 20 % each by construction: **about 6,000 `up` and 6,000 `down` events, about 18,000 `mid`**. | Per thesis, at least the floor below |
| (c) power | Kish effective number of entry-date clusters per quarter. Months with at least one event. | Earnings seasons bunch entries into about 6 weeks a quarter. The assumed correction is raw / 4 (same-date clustering times overlap). The count replaces the assumption with the measured Kish figure. | see below |
| (d) gross versus cost | Not a count: the prior arithmetic below | | **the binding item** |
| (e) matched benchmark | Share of `up`/`down` events with at least 5 `mid` peers in the same size tercile within ±5 sessions (no returns needed: membership and dates only). | High in season, thin off-season. | At least 80 % matched |
| (f) delisted coverage | Of universe events, the share whose security has no bar on the scheduled 60th session (an early exit), and of universe issuers that leave in the window, the share with bars to their last listed session. | The universe is ranked on bars, so entry bars exist. Early exits are mostly mergers. | If most leavers lack bars, any kill is "killed on survivor data" |

### The minimum the floor needs
- The sd of a 60-session matched spread is **assumed** at about 1,500 bp. This is UNGRADED: typical idiosyncratic volatility of 30 %/yr over a quarter, plus peer noise. Measuring it would read returns.
- The kill t is on the **net** spread. Costs move the mean, not the sd. For a true net effect at the floor (+30 bp) to reach t = 1 takes n_eff ≥ (1500 / 30)² ≈ **2,500 effective, about 10,000 traded events per thesis** at the /4 correction. The expected ~6,000 per thesis falls short. With ~6,000 traded (n_eff ≈ 1,500), the SE is about 39 bp, so the t floor binds above the +30 bp mean floor: a pass needs a net mean of about +39 bp.
- That makes the read **under-powered at its own floor**. A FAIL can rule out a net effect well above +39 bp, not one near +30. The UNDERPOWERED line (2,500 traded) and the `killed_low_power` band (2,500 to 10,000) in section 3 encode this.
- At the plausible effect it is worse: the long thesis's plausible net effect is negative at 25 bp (below).

### Item (d), the prior arithmetic (SH-6, size-screened CZ, 2010-2024, already post-publication, so no further QI-2 haircut)

| Thesis | Monthly | × 2.86 months (60 sessions) | Round-trip cost at 25 bp | At 15 bp | Item (d) |
|---|---|---|---|---|---|
| L (long vs mid) | long leg +0.07 %/mo over the average, about half of the 0.25 % L/S for long against the middle: 0.07 to 0.12 | **≈ 20 to 35 bp** | 50 bp | 30 bp | **fails at 25; borderline at 15** |
| S (short vs mid) | short leg ≈ 0.25 − 0.07 = 0.18 %/mo | **≈ 51 bp** | 50 + 7.1 borrow = 57.1 | 30 + 7.1 = 37.1 | **fails at 25; met at 15** |

Two caveats:
- Change 1 (early drift that CZ leaves out) is the only thing that could lift these numbers. It is UNGRADED.
- Holding the CZ monthly rate for 2.86 months assumes the drift does not fade inside the hold, which is generous.

The **floor's** gross requirement (80 to 87 bp) is above every row. On graded evidence the read is **more likely to FAIL than to pass, and the most likely reading of a FAIL is "cost", which the B9 note already established**. That is the honest case against spending a candidate slot now.

## 5. What must be built first, with sizes

| # | Item | Who | Size | Class | Notes |
|---|---|---|---|---|---|
| 1 | **T164e's real-store run and switch flip** (`edgar.filing_events_enabled = true`, one EDGAR ingest that fills `filing_events`, the four pass conditions of the spec's T164e paragraph, the default flipped in a small PR) | owner, in the post-seed data sitting | S | B (config) | **This is why `filing_events` is empty.** The code merged in PR #1459. The switch defaults to `false`, and the run waited first on `restamp-1382a`, then lost its consumer when B9 was killed. The items refresh (`EdgarSource._refresh_items`) reads the cached `submissions.zip` and its older pages under `reuse_cached` with no request, so it is cheap. Before it: confirm that `restamp-1382a3` (STATUS, post-span data sitting) is done or does not touch universe 8-K stamps. Confirm too that the incremental session path (T323, after T324) still asks `filing_events` nightly. If it does not, the table goes stale after the one run. That is a question for the T324 sitting, and the answer may be a size-S task. |
| 2 | **Event builder probe** `~/tradepartner-probes/earn202/build.py`: `counts` (section 4, no returns) and `frame` (the dataset CSV, run only after the freeze) | agent | M (about 300 lines) | outside the repo (C1b precedent) | Uses `filing_events_as_of`, `first_known`, `universe_as_of`, `session_close` and the bar reader. A test on the fixture store for rules 2 and 3: the 16:00 to 17:30 trap, a half day, a weekend acceptance, an 8-K/A and a duplicate within 20 sessions. |
| 3 | **Experiment file** `docs/experiments/e1-earnings-reaction-matched.md` (the `[study]` table above, the frozen line's text, the B9 disclosure) | agent | S | B (`docs/experiments`) | Only after section 4's counts pass (stage 4a: no experiment file before the check). |
| 4 | **A per-line entry window in the runner's kill lines**: an optional `entry_start` key on `[[study.kill]]` (`experiment.py` `_KILL_KEYS` and validation; `run.py` `summarise_study` filters `selected` on `entry`), with tests that an absent key changes nothing (C1b and C2 goldens unchanged) | agent | S | A (needs a plan line, or the size-S exemption) | **Needed before the freeze**, so the 2020+ cells are registered kill lines and the registry flag carries the verdict. No unversioned script decides anything. Review: quant-auditor. |

There is no family code, engine change or paper dispatch. The registered event-study runner (`experiment study`) already does entry, matched peers, costs, borrow, overlap-robust clustered t and kill lines.

**Order once T324's seed is done:** item 1 (owner) → item 2 `counts` → ledger row with the 4a result → (if `pass`, or if the owner accepts (d)) item 4 merged → the freeze comment → item 2 `frame` → item 3 registered → one run. Items 2, 3 and 4 can be written now. The first store read waits on item 1.

## Open questions for the owner

1. **4a(d) and power.** On graded evidence, both theses fail item (d) at today's measured cost, and the read is under-powered at its own +30 bp floor. Spend a candidate slot anyway, on the UNGRADED front-loaded drift? Or defer until the buy-timing cost work measures these names? Recommendation: run item 1 and the counts now (cheap, no returns), and defer the return read until the cost figure exists. If the cost does not come down, record `deferred: 4a(d) cost`.
2. **Short side.** Is a short thesis with 30 bp/yr borrow acceptable as a decision cell (ADR 0024), with 300 bp/yr reported only?
3. **Dedup rule 7** keeps the first of two 2.02 8-Ks within 20 sessions, so a pre-announcement wins over the release. The alternative is to drop both. Either is fine if fixed before the freeze.
