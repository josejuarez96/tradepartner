# E1 design: earnings reaction against matched announcers, every choice pinned

**Issue:** #3503  ·  **Date:** 2026-11-16  ·  **Kind:** frozen design and kill line for the first idea on the 2026-11-16 route ([ADR 0023 amendment](../decisions/0023-break-first-fast-books-paper-exam.md#amendment-2026-11-16-3496-own-store-read-first-published-evidence-as-the-prior), table row 1)  ·  **Agent:** team e1prep, claude-opus-5-5  ·  **Reads:** code and docs only. No store was opened, no return of any kind was read, nothing was registered.

This file pins the [earnings-family design](2026-11-02-earnings-event-family-design.md) (#2892) under the new route. Where the two differ, this file governs and says why. The kill line in section 4 is posted verbatim on #3503 before any return is read; that comment's time is the freeze. Stage 4a comes first: no experiment file is drafted, and no return is read, unless the counts in section 5 pass ([research program 4a](../ways-of-working/research-program.md#4a-feasibility-check-before-an-experiment-file)).

## 1. What changed from the 2026-11-02 DRAFT, and why

| DRAFT (#2892) | Pinned here | Why |
|---|---|---|
| Two theses (long `up`, short `down`), each its own chance | **One decision thesis: long `up` against `mid`.** The short `down` thesis is printed, never decisive | The route allows one decision cell per idea. A short pass has no road to the exam: the route's next step is a counted backtest trial (ADR 0023 amendment, rule 3) and the engine refuses shorts; the live book is long-only (ADR 0024) |
| 25 bp per side in the line | **50 bp per side** in the line (the harsh cell) | Amendment rule 2: the decision cell is at ≥ 50 bp per side |
| Window from 2016-07-01 | **Entries from 2019-01-02** | Table row 1: until T184h fills the 2016-2018 delisters, the window starts in 2019. A later start after T184h is a new registration that discloses this one |
| Delisting: last close carried | **`delisting_return = "failure_v1"`**, the last-close result printed beside it | Amendment rule 2 (survivorship) and the [registry spec's 2026-11-16 amendment](../specs/research-registry.md) (#3498) |
| UNDERPOWERED and `killed_low_power` outcomes | **Removed.** Power is decided before the read by stage 4a (c) (section 5); a read that runs has two outcomes | One combine rule. An underpowered idea never reaches a read (`deferred: power`) |
| N recorded as 4 configurations | **N = 2** configurations (two kill lines of one thesis); **k = 51** printed as N + k = 53 | Amendment, "Multiple testing" |

Unchanged from #2892: the event (8-K Item 2.02 at its EDGAR acceptance), rules 1 to 9 of its section 2, the matched-peer design, the 60-session hold, the +30 bp net floor and t ≥ 1.0, the 2020+ sub-window cell, and the disclosure that this is **B9 again with four changes** (B9's two looks are in k).

## 2. Events (the builder's rules, pinned)

The builder's point-in-time core is a probe outside the repo, like C1b's builder. `research/` may not name `filing_events` until T164e lifts that guard (`tests/store/test_schema.py::test_no_trial_path_reads_filing_events`), and it may not import `store.filing_index` (LLM boundary rule iii). The core is `~/tradepartner-probes/earn202/earnings.py` (`day_zero`, `disseminated_on`, `session_events`, `dedupe_releases`, `power_counts`), SHA-256 `389ef778ecfd90f00f5eb27bfe502f6a9a810a9aecf4042aeb6db770d6ee53e0`. It is tested on a synthetic in-memory store by `~/tradepartner-probes/earn202/test_earnings.py`, SHA-256 `d38a05312393cdd59fc9d0b7e226a26d9e71ccee4c7aa2b48dcdee0605bd7485`, which you run from a checkout with `uv run pytest ~/tradepartner-probes/earn202/test_earnings.py`. The frame step (rule 3's reaction, rule 4's groups, the class choice, and the CSV) is written after the freeze and implements exactly what is below. Its SHA-256 is recorded when the dataset is registered.

1. **Source.** `filing_events_as_of(conn, session_close(s0), forms=["8-K"], items=["2.02"])`: whole-code item match, `known_at = accepted_at`. `8-K/A` is never an event; it is counted (`n_8ka`).
2. **Day 0 (`s0`).** `D` is the New York date of `accepted_at`. If `D` is an XNYS session and `accepted_at < session_close(D)`, then `s0 = D`; otherwise `s0 = next_session(D)`. A half day uses its own close, and an acceptance at exactly the close belongs to the next session. A filing is read only at `session_close(s0)` or later, so **an 8-K accepted after a session's close is never an event of that session** (the probe tests' no-look-ahead and truncation cases).
3. **Universe.** The event's class must be a member of `universe_as_of(conn, session_close(s0))`.
4. **Stamp cross-check.** If `filing_events.accepted_at` differs from `filing_index.first_known(conn, session_close(s0), ciks=…, form="8-K")`'s stamp for the same `(cik, accession)`, the event is dropped and counted (`n_stamp_mismatch`). Any non-zero count stops the read until it is explained (#1382).
5. **EDGAR dissemination.** `disseminated_on` is the New York acceptance date when that day is an EDGAR business day (a weekday that is not a US federal holiday per `pandas`' `USFederalHolidayCalendar`, and not one of the executive-order closures pinned in `EDGAR_EXTRA_CLOSURES`: 2018-12-05, 2018-12-24, 2019-12-24, 2020-12-24) and the time is before 17:30; otherwise it is the next EDGAR business day. An event with `disseminated_on > s0` is dropped and counted (`n_edgar_holiday`).
6. **One event per release.** Releases are taken per `(cik, accession)`. Within a CIK, a release whose `s0` is at most **20 sessions** after the CIK's last **kept** release's `s0` is dropped and counted (`n_dup_20s`). The first stands. Only releases that pass rules 1 to 5 count as earlier releases. A kept release that rule 7 or 8 later drops still blocks the next 20 sessions, because the dedupe runs before them.
7. **Class choice.** When a CIK has more than one member class, the event uses the class with the highest median raw dollar volume (raw close × volume) over the 20 sessions ending `previous_session(s0)`, with at least 10 bars. Ties go to the lowest `security_id`. A CIK with no class meeting 10 bars is dropped and counted (`n_no_class`).
8. **Reaction `R`.** `R = adj_close(s0) / adj_close(previous_session(s0)) − 1 − (the same for SPY)`, using dividend-adjusted bars read at `close(2023-12-29)`. An event missing any of the four closes gets no label (`n_no_label`) and joins no pool.
9. **Groups (causal).** The pool for an event is every kept event with an `R` (rule 8) whose `τ = session_close(s0)` falls in the 63 sessions before the event's `s0` (strictly earlier `s0`; same-`s0` events are excluded). If the pool has fewer than 400 events, the event gets no group and is counted as `n_small_pool`. It still enters later events' pools. `p20` and `p80` are the pool's 20th and 80th percentiles, linear interpolation (`numpy.quantile`'s default). `up` if `R > p80`, `down` if `R < p20`, `mid` otherwise (ties go to `mid`).
10. **Window.** Day 0 runs from **2018-12-31** (entry 2019-01-02) to **2023-10-04** (entry 2023-10-05, whose 60th session is 2023-12-29). Events with `s0` from 2018-09-28 onward feed the pools and the 20-session dedupe only and are not emitted. Nothing after `close(2023-12-29)` is read.
11. **Output row.** `event_id = accession`, `security_id`, `known_at = τ`, `group`, `stratum = "8-K"`, `issuer_key = cik`, plus `accepted_at`, `s0` and `R` for the record. Dataset name `e1-earnings-reaction-events`, event column `known_at`, split `full`.

## 3. The runner design (the experiment file's `[study]` table)

Slug `e1-earnings-reaction-matched`, `kind = "return"`, stage 2, `family = "earnings_drift"` (B9's root family, proposed and never registered; it must be added to `hypotheses.families` before registration), `confirmatory = false`, `provenance = "deterministic"`, `seed = 20261116` (nothing is random), `[window] start = 2018-12-31`, `end = 2023-12-29`, `budget.runs = 1`, `budget.configurations = 2`.

```toml
[study]
configurations = 2
horizons = [20, 60]
groups = ["up", "down", "mid"]
peer_group = "mid"
entry = "next_open_after_instant"
facts_read = "event_known_at"
matching = "entry_known"
delisting_return = "failure_v1"   # parses once T413 merges (#3498); the file waits for T412 too
bucket_by = ["quarter", "stratum"]
peer_by = ["stratum"]
n_buckets = 3
min_bucket_events = 30
price_floor = 5.0
volume_floor = 10000000.0
volume_lookback = 20
min_volume_bars = 10
shares_stale_days = 400
shares_fact = "shares_outstanding"
benchmark_security = "SPY"         # a beta regressor only, never a benchmark
peer_window = 5
min_peers = 5
near_k = 10
beta_lookback = 120
beta_min_bars = 60
cost_bps = [8.8, 25.0, 50.0, 60.0] # arrival over $200M, base, harsh (decides), paper basis
borrow_bps = [30.0, 300.0]
z = 1.645                          # one-sided 95 %
cluster_by = ["month", "quarter"]

[[study.kill]]
name = "long"
side = "long"
group = "up"
horizon = 60
readings = ["raw", "beta_adjusted", "size_tight", "size_tight_beta_adjusted"]
metric = "net_bp"
floor = 30.0
t_metric = "net_bp"
t = "min"
t_floor = 1.0
cost_bps = 50.0
borrow_bps = 30.0                  # unused by a long line; must name a declared value

[[study.kill]]
name = "long2020"
side = "long"
group = "up"
horizon = 60
readings = ["raw"]
metric = "net_bp"
floor = 30.0
t_metric = "net_bp"
t = "min"
t_floor = 1.0
cost_bps = 50.0
borrow_bps = 30.0
entry_start = 2020-01-02           # the per-line entry window (this PR)

[multiplicity]
method = "none"
family_id = "e1-earnings-reaction"
family_size = 53                   # N + k = 2 + 51, printed, never a gate
```

The kill t is the runner's overlap-calibrated t on `entry_index` (flat Hansen-Hodrick kernel, lag `60 − 1 + 2 × 5 = 69` sessions). With `t = "min"` it is that one t; the month and quarter t's are reported only. `R` is a label input: a return that ends at `close(s0)`, before entry. It is computed only by the frame step, after the freeze, and nothing about it is printed before the run except counts. Each `[[study.kill]]` line fires if any listed reading has a net mean below +30 bp or a t below 1.0, and a reading that cannot be computed fires it (`evaluate_kill`).

## 4. The kill line (posted verbatim on #3503 before any return read)

> **E1 kill line, frozen.** Idea E1, earnings reaction against matched announcers, B9 again with four changes (disclosed). Design: `docs/research/2026-11-16-e1-earnings-reaction-design.md` at the commit this comment names; its sections 2 and 3 are part of this line.
>
> - **Decision cell.** Long the `up` group (reaction `R` above its causal p80) against size-tercile-matched `mid` announcers entered within ±5 sessions. 60-session hold, entry at the open after `session_close(s0)`. Net of **50 bp per side** (100 bp a round trip) on the treated leg; the matched leg is costless (an idealisation that only weakens a pass). Delisting exits under `failure_v1`.
> - **Window.** Entries 2019-01-02 to 2023-10-05; nothing read after `close(2023-12-29)`.
> - **Line `long`.** The mean net spread is at least **+30 bp** per event **and** its overlap-calibrated t is at least **1.0**, on **each** of the four readings (raw, beta-adjusted, size-tight, size-tight beta-adjusted).
> - **Line `long2020`.** The same test on the raw reading over entries from **2020-01-02** only.
> - **Combine rule (the only one).** **PASS** if and only if the registry's `kill_long` and `kill_long2020` are both 0. Otherwise **FAIL**. A reading that cannot be computed is a fail. Equality passes, and unrounded values are compared.
> - **Before the read.** Stage 4a must pass, as pinned in section 5 of the design: (a) `n_stamp_mismatch` and the run row's `filing_events_clock_suspect` are 0; (c1) the counts script's `n_eff ≥ 2,500` on its upper-bound N; (c2) the same formula on the pre-return pass's tradable, matched `up` count; (e) the pre-return pass matches at least 80 % of tradable `up` events; (f) the delister coverage is at least 80 % in every year from 2019 to 2023. Any failure means no return is read: `deferred: power`, `deferred: matching` or `deferred: coverage`. The window is not moved without a new frozen line.
> - **Printed, never deciding.** Net at 8.8, 25 and 60 bp per side. The break-even cost per side (gross spread / 2) for each reading. The last-close (`none`) result beside the `failure_v1` one. The short `down` thesis at 50 bp per side with 30 and 300 bp/yr borrow. The 20-session horizon. Raw and SPY-adjusted means. Counts by acceptance time (pre-open, intraday, after close), `n_dup_20s`, `n_8ka`, `n_stamp_mismatch`, `n_edgar_holiday` and `n_delisted_exits`. Each year's mean spread. The summary's `kill_short` is the runner's fallback line, not a thesis, and is labelled as such. **N = 2, k = 51 (a lower bound: every return-touching look since 2026-10-30 outside this family's N, plus B9's two files), N + k = 53.**
> - **Outcome.** A PASS goes to rung 5 placebos, then the route's counted in-sample trial at the same harsh settings. It is not net alpha. A FAIL retires the design under this slug. Either way it counts as one candidate under the third limit. A later look is a new registration that discloses this one and B9's two looks.

## 5. Stage 4a, before anything reads a return

| Item | How | Gate |
|---|---|---|
| (a) causal timestamp | `filing_events` filled by T164e's real-store run, plus `n_stamp_mismatch` from the counts | mismatch 0 and clock-suspect 0 |
| (b) events per year | the counts script, `events_by_entry_year` | an input to (c) |
| (c1) power, upper bound | the counts script below, before any price is read | `n_eff ≥ 2,500` |
| (c2) power, tradable | the pre-return pass below, on the frame CSV | `n_eff = (matched up count) / (2 × B)`, with `B` from the counts script, at least 2,500 |
| (d) gross versus cost | the rung-1 screen: plausible gross 20 to 35 bp per event (SH-6, #2892 §4) against a round trip of 2 × 8.8 = 17.6 bp at the arrival measure over $200M | **passes the screen**. The harsh cell (100 bp) is the read's job, and on the prior it fails |
| (e) matched benchmark | the pre-return pass below | at least 80 % of tradable `up` events matched |
| (f) delister coverage | read-only, below | at least 80 % in every year 2019-2023 |

**Power, pinned.** Per thesis, `n_thesis = 0.2 × N` (the tails take 20 % each by construction; group sizes need `R`, which counts never compute). `B` is the season bunching: the window's entry sessions divided by the sum over calendar quarters of each quarter's Kish count of entry dates, `(Σ n_d)² / Σ n_d²`. `B = 1` when entries spread evenly, and about 2 when they bunch into six weeks of a quarter. `n_eff = n_thesis / (2 × B)`. The factor 2 is the overlap allowance of #2892's raw/4 that bunching does not explain. It is UNGRADED and pinned now. The need is `n_eff ≥ (1,500 / 30)² = 2,500` (sd of a 60-session matched spread assumed at 1,500 bp, UNGRADED; floor +30 bp). **Expectation:** about 4,000 to 4,500 eligible events a year over 4.75 years is about 20,000, so `n_thesis ≈ 4,000` and `n_eff ≈ 1,000` at `B ≈ 2`. **E1 is expected to be `deferred: power`**, as table row 1 of the amendment says. The counts decide; nothing else does.

**The pre-return pass ((c2) and (e)), pinned.** It runs on the frame CSV, after the freeze and before `experiment open`. It reads no bar dated on or after an event's entry session. For each `up` and `mid` event it applies the runner's pre-entry rules with the `[study]` values above: the raw close of `previous_session(entry)` at least $5; median raw dollar volume over the 20 sessions before entry at least $10M with at least 10 bars; a `shares_outstanding` fact through `facts_as_of` at the event's `known_at`, at most 400 days old; at least 60 return pairs in the 120 sessions before entry (a bar count, not a beta); size terciles under `matching = "entry_known"`; and at least 5 tradable `mid` peers in the same tercile and stratum with entries within ±5 sessions (other events of the same `issuer_key` excluded). It prints the tradable `up` count, the matched `up` count and the matched share. The SHA-256 of the pass is recorded beside the frame's. A gap between this pass and the runner's own counts at the run is reported, never used to change the verdict.

**Delister coverage ((f)), pinned.** The denominator is every security that `listing_ends_as_of(conn, close(2023-12-29), settings)` reports as `delisted` with an `end_session` in calendar year Y, 2019 to 2023, and that was a member of `universe_as_of` at `session_close(previous_session(end_session))`. Every reason counts (failure, bankruptcy, merger, unknown). A `transferred` row is not an end, and a ticker change is not a listing end. A security is covered when it has a `prices_daily` bar dated `end_session`. An `end_session` of None counts as not covered. The year's share is covered over denominator.

**The counts script.** `~/tradepartner-probes/earn202/counts.py`, SHA-256 `42d89ad89c0f15b7905db71081d011dcb0593969a10bf7c9b6ae76948de899ab`, outside the repo (C1b precedent). It imports the probe core beside it and runs from a checkout at the commit that merges this file. It reads filings and universe membership only, never a price or a return, at `session_close(s0)` for every `s0` from 2018-09-28 (rule 10's warm-up, so its dedupe matches the frame's) to 2023-10-04, and asserts that no read is after `close(2023-12-29)`. It refuses the configured store path. Run it once, on a store copy, after T164e:

```bash
uv run python ~/tradepartner-probes/earn202/counts.py --store <copy.duckdb>
```

It prints JSON: window, events by entry year, the acceptance-time split, `n_dup_20s`, `n_stamp_mismatch`, `n_edgar_holiday`, `n_8ka`, the power block (`n_events`, `n_thesis`, `kish_dates`, `bunching`, `n_eff`, `months_with_events`, `pass`), #2892's raw/4 figure for comparison, and `verdict_4a_c`. The result goes in E1's ladder-ledger row. Its `N` is an upper bound: it is counted before the price, liquidity, shares, beta and peer filters, so (c2) repeats the gate on the tradable count. A smoke run on an empty synthetic store took about 40 s; the real store's per-session `universe_as_of` will take longer. That is a one-off cost, not a rebuild.

## 6. Dependencies before the return read

1. **T164e's real-store run** (owner): fills `filing_events`. The counts run after it.
2. **T413** (`delisting_return` parses) and **T412** (the runner applies it). The last-close result has to be printed beside the `failure_v1` one. If T412's artifact cannot give it from its event rows, a size-S follow-up adds it before the experiment file is registered. It is never done by a second run.
3. **`earnings_drift` in `hypotheses.families`** (config, class B) before registration.
4. **The frame step** (rules 3, 4 and 7 and the CSV), written after the freeze. Its SHA-256 is recorded at `dataset register`.
5. **This PR's `entry_start` key**, so that `long2020` is a registered kill line.

## 7. Prior and disclosure

- **Prior (rung 2 as the prior):** SH-6 MIXED. About 20 to 35 bp gross per 60-session event for the long leg, and about 51 bp for the short (#2892 §4). The long thesis needs at least 130 bp gross to pass at 50 bp per side. **On the prior, the read is far more likely to fail than to pass.** It may pass only if the early drift that the monthly yardstick leaves out (change 1, UNGRADED) is large.
- **Second look:** B9 (2026-10-18, killed at rungs 1 and 2 on two CZ files) is the same mechanism. Its window (CZ 2010-2024) contains these months, so the read is not out of sample against B9's yardstick.
- **Window overlap:** C1 and C1b used other filings over the same months. Only their peer method is reused.
