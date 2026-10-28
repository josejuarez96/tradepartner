# Experiment: C2 spin-off children, size-matched: entry 2 weeks after the first regular-way session, 6 and 12 month holds

**Kind:** return  ·  **Stage:** 2  ·  **Author:** team c2spin (issue #2400), for the owner  ·  **Date:** 2026-10-27

**Status: design only. Do not register this file yet (see [The gate](#the-gate-do-not-register-below-it)).** Merging this file does not register it. No return of any kind has been computed when this text was written; the only reads are the counts in the feasibility section. The script lives outside the repo (`~/tradepartner-probes/c2spin/c2_spinoff_children.py`); the registry hashes the whole file, so settle the text first. It copies [C1b](c1b-filing-delay-matched.md)'s working pattern and its lessons, below.

## What this tests, and what it learned from C1

Candidate C2 of the [sharper-candidates report](../research/2026-10-27-sharper-candidates.md): small children of corporate spin-offs are sold by holders who may not own them (wrong index, too small, wrong sector) in the first weeks, before analysts cover them. Buying after that selling should earn a premium over comparable stocks. The report grades the evidence MIXED (Tier 1 to 2013; a Nordic 2000-2020 study with no effect; CSD, the only open long-leg proxy, reportedly lagged the S&P 500 after its 2016 index change).

C1 (against SPY) and C1b (against matched peers) taught five things; each is binding here:

1. **SPY is never a benchmark.** It is only a beta regressor. Every spread here is against matched peers.
2. **Kill lines are on spreads against a matched benchmark** (size-matched, and beta-adjusted), net of costs, not absolute.
3. **Classification uses only what is known at the time.** A child is "new issue" from its first-bar date relative to the ex-date, known by the entry session (all 21 kept spin-off rows have a `known_at` before the entry session); peers are chosen from bars, volumes and share counts dated before the entry session. Disclosed exceptions: the child's share count when the parent-ratio route is missing (`n_size_ex_post`), the `size_conflict` check against the child's later first-reported count, the in-hold betas (kill-only), and peers' spin-off price adjustments that use rows known by the cutoff.
4. **The survivorship caveat is stated** ([Prior evidence](#prior-evidence-disclosure-and-n)).
5. **A new root family.** `spinoff` is experiment-only, added by PR #2401 (issue #2399) as `filing_delay` was. The file below uses `family = "spinoff"`; it cannot register until #2401 has merged.

## Step 1 (rung 2): CSD against RSP. Not runnable: neither ETF is in the store

Read-only, 2026-10-27: the store's `listings` hold SPY among the ETFs and **neither CSD (Invesco S&P Spin-Off ETF) nor RSP**. So no CSD experiment is pre-registered. What a CSD read would take, all the owner's call:

- `uv run tradepartner backfill-benchmark CSD --since 2016-01-01 --cik ... --name ... --exchange ...` per the T243 runbook (the same command T246 names for RSP, which is also still to do; the owner runs it). It is a vendor fetch of one ETF's bars and dividends.
- **The report's window cannot be met.** The store's bars start 2016-01-04, so "2007 to 2019" would be 2016 to 2019: four years, spanning CSD's May 2016 index change that the report says cut its returns. The 2007-2015 part needs bars the store does not hold.
- The read would be CSD's excess total return over RSP, kill if below 0 or t < 1.0. Caveat as in the report: CSD holds only larger spin-offs, so a kill leaves the small-child niche untested, and four years of daily excess returns give a t that says little. **Verdict on step 1: low information, cheap, parked until the owner fetches.**

## The event list: what the store holds

- **Form 10-12B registrations are not stored as a form-typed list** (read 2026-10-27). `10-12B` is in `master.issuer_forms` and the master takes each new CIK's `known_at` from its earliest issuer-form acceptance, which for a fresh spin-off CIK is usually its 10-12B; but the form type is not kept (`filing_events` is for 8-K items only and has 0 rows; `statement_facts` holds only 10-Q and 10-K families). The report's "the Form 10-12B registration is in the store with its acceptance time" is therefore true only of the CIK's first acceptance instant. Registrations per year: **not countable from the store**.
- The event list is therefore the `corporate_actions` `spinoff` rows (source `alpaca`): child in `security_id`, parent in `source_action_id`. They start **2020-11-17** (nothing earlier), 88 rows to 2026-10-02. Many large spin-offs are absent, so the list is partial (coverage of the source is UNVERIFIED).
- **Late-known rows.** 13 of the 34 in-window new-issue children (by ex-date) have a row whose `known_at` is 2024 or 2025 (a backfill), after the development boundary. Reading them would read data from after the boundary, so **this design reads only rows known by close(2023-12-29), through `live_actions_as_of`.** That costs 13 events and is the honest count.

## The design

**Events.** Each `spinoff` row known by close(2023-12-29), ex-date from 2020-11-01 to 2023-12-29. **New issue:** the child's first bar lies from 30 days before to 7 days after the ex-date (a child with an older bar is a re-labelled existing name: the store has such rows, e.g. an old company shown as "child"; they are `preexisting`, dropped and counted). `S0` = the first XNYS session on or after the ex-date (the first regular-way session; bars before it are when-issued).

**Entry and outcome.** The open of the 10th session after `S0` (about two weeks, the middle of the pre-declared 1 to 4 week range; one point, chosen before any return, so there is no search over it). Entries at +5 and +20 sessions are **reported, not decisive**, and are not configurations. Exit at the close of the 126th (6 months) or 252nd (12 months) session, the entry session counted as the first. Dividend-adjusted frame, all reads from the as-of API at `t = close(2023-12-29)`. A name with no bar after its last bar is held at its last close (delisting rule in value; `*_exited_early_n` counts them). Events whose exit falls after 2023-12-29 are dropped at that horizon.

**Tradability.** For the child: raw close of the prior session at least $5, median dollar volume of the up to 20 prior sessions at least $1,000,000 over **at least 5** bars (a child entered in week 2 has 10 bars at most). For peers: the same, over at least 10 bars.

**Size.** Market cap = shares outstanding times the prior session's raw close. Peers' shares: the company-level (`class_member` empty) `shares_outstanding` fact, newest row with `known_at` at or before the prior session's close and an as-of date within 400 days. **The child's shares are the problem:** only 4 of the 34 in-window children (by ex-date) have such a fact known by the entry. So the child's share count is, in order: (1) **the parent's company-level shares known at the prior close times the spin row's `ratio_or_amount`** (point in time; available for 19 of the 21 kept events; implied-over-first-reported child shares has a median of 1.00 but a range of 0.15x to 6.9 million x, so the ratio field is unreliable for some, so an event whose ratio-implied count is more than 2x off the child's first reported count is dropped as `size_conflict`; that check reads a later fact, but only to reject a bad data field, never an outcome. The log-cap gap rule alone does not catch a wrong ratio, because the nearest peers follow the wrong cap), flagged `size_parent_ratio`; (2) else the earliest company-level row the store holds for the child, known weeks after entry, flagged `size_ex_post`. Both counted (`n_size_parent_ratio`, `n_size_ex_post`); (2) is a matching variable that reads no return but is **not point in time**. `*_logcap_gap` reports how well matching worked.

**Peers (the benchmark).** On the entry session, every security with a bar at the entry open, a prior-session raw close of at least $5, the liquidity floor above, at least 200 bars in the 252 prior sessions (seasoned: not a recent listing), a known share count, that is not a spin-off child in the list, not the event's parent. Peers' prices are adjusted for spin-offs known by the cutoff (`include_spin_offs`), so a peer that is itself a parent carries no price drop; **residual bias, stated:** a peer that spun off a child the list does not hold (the list is partial, and the 13 late-known rows are not read) keeps an unadjusted drop, which lowers peer returns and raises spreads, flattering a pass. Peers are the **same session's** names, held over the same entry-open to exit-close span, so the market level cancels exactly. Two peer sets by nearness in log market cap: **cell** (the 100 nearest) and **tight** (the 20 nearest). Fewer than 20 eligible peers or 10 tight ones drops the event (`n_dropped_no_peers`); a child whose mean log cap is more than ln 2 (a factor of 2) from its tight peers' is dropped as unmatched (`n_dropped_no_size_match`).

**The four readings.** `cell`, `tight`, and each beta-adjusted (`cellb`, `tightb`): `spread_b = R_i - mean over peers of R_p`, where `R` compounds the daily returns minus beta times SPY's daily return over the hold (entry day open to close included), with SPY used only as a regressor. **A causal pre-entry beta is impossible for a new listing** (it has 5 to 20 bars before entry), so beta is the OLS slope of daily dividend-adjusted returns on SPY's **over the hold itself**, for the child and every peer alike. That is ex-post, and it is allowed only because the line is kill-only: it can only add a hurdle. It removes the covariance with the market, not the intercept. `*_beta_gap` (child minus tight peers) is reported. **Every kill line must hold on all four readings, or it fires.**

**Not removed, stated.** An industry is not matched (the store's classification of a brand-new child is thin); a child spun from a stressed or a high-growth parent can differ in style from its size peers. Parent returns are reported (`*_minus_parent_bp`: child minus its own parent, same span) and are in no kill line; the parent is not size-matched.

## Kill lines (pre-declared; every one on the spread, net of costs)

Per horizon (6 months and 12 months, two configurations), on each of the four readings, with 30 bp a side (a 60 bp round trip) on the child leg; the peer leg is taken as free (flatters the result, so a pass is an upper bound):

- **Killed** if the mean gross spread is **below 60 bp** (net of 30 bp a side below 0) **or** t of the net-of-30-bp spread is **below 1.0**, on **any** reading. t is the smaller of the month- and quarter-clustered t (clusters by entry month and entry quarter, no small-sample correction); a statistic that cannot be computed counts as a kill.
- **The 60 bp bar is not what binds here.** With spreads that vary by tens of percent across events, the t bar needs a mean spread of roughly one standard error: **7 to 18 percentage points** at the counts below.
- **The thesis passes only if both horizons survive.** Reported, not decisive: 15 and 50 bp a side, entries +5 and +20, raw and SPY-adjusted means, child minus parent.
- **Idealisation and an upper bound.** Overlapping 6 and 12 month holds understate the clustered standard errors, so a pass's t is an upper bound.
- **No kill line can pass on size, market or beta alone:** the market level cancels (same-session peers, same span); beta is removed by the beta-adjusted readings, which must also pass; size is matched at 20 and 100 neighbours with the gap reported and an unmatched child dropped. Residual: the child's share count is ex-post, and the style differences above.

## The gate (do not register below it)

**Status of the verdict.** A horizon with fewer than **30** traded events is `underpowered`: the statistics are printed, but it is neither a kill nor a pass. With 30 to 59 events a kill is `killed_low_power`: recorded, and it **retires nothing** (the table below gives about a 25 to 30% chance of t >= 1 at a real effect with 34 events). Only a kill with **60 or more** traded events is `killed`, and even then it is a **bounded** kill: even at 60 events a real +2 to +4 pp effect is missed about two times in three, so a `killed` means only "effects at or above `*_tight_mde80_bp` (net of 30 bp), printed beside the verdict (the largest clustered standard error over the four readings, times 1.84), are excluded", and it retires that range, not the thesis. The thesis verdict is `killed` if any horizon is killed, else `killed_low_power`, else `underpowered` if any horizon has fewer than 30, else `pass`. The numbers below say the run will be `underpowered`.

**Power, stated plainly.** Event counts before the price, liquidity, size, peer and horizon filters (read-only `counts` subcommand, 2026-10-27):

| Entry | Known-by-boundary spin-off rows, 2020-11 to 2023-12 | New-issue children | 6-month hold readable by 2023-12-29 | 12-month hold readable |
|---|---|---|---|---|
| +10 sessions | 25 (4 are pre-existing names) | 21 (2020: 1, 2021: 4, 2022: 3, 2023: 13) | **12** | **8** |
| +5 / +20 | | | 13 / 12 | 8 / 7 |

For reference, counting every row regardless of `known_at` (which this design refuses): 34 new-issue children, about 24 readable at 6 months and 17 at 12 months. The tradability, size and peer filters cut further; the run counts them.

Descriptive arithmetic, **assumed** standard deviations of a child's spread (0.35 at 6 months, 0.50 at 12 months, chosen from typical small-cap idiosyncratic volatility, not read from data) and unclustered:

| Events | 6m: SE / t=1 needs / MDE (80% power) | chance of t >= 1 at a +2 pp effect (gross; upper bound: net of the 0.6 pp cost the chance is lower) | 12m: SE / MDE | chance at +4 pp (same) |
|---|---|---|---|---|
| 8 | 12.4 / 12.4 / 22.8 pp | 0.20 | 17.7 / 32.6 pp | 0.22 |
| 12 | 10.1 / 10.1 / 18.6 pp | 0.21 | 14.4 / 26.6 pp | 0.23 |
| 21 | 7.6 / 7.6 / 14.1 pp | 0.23 | 10.9 / 20.1 pp | 0.26 |
| 34 | 6.0 / 6.0 / 11.1 pp | 0.25 | 8.6 / 15.8 pp | 0.30 |
| 60 | 4.5 / 4.5 / 8.3 pp | 0.29 | 6.5 / 11.9 pp | 0.35 |
| 120 | 3.2 / 3.2 / 5.9 pp | 0.35 | 4.6 / 8.4 pp | 0.45 |

The plausible effect (the literature's several percent a year, cut by the 58% publication haircut, QI-2) is 2 to 4 percentage points. **With 8 to 12 events the test cannot reach t >= 1.0 at a plausible effect (about a one-in-five chance), and a "kill" would say nothing. Even 120 events give under a coin flip.** Only an effect above roughly 10 to 20 points would show. **Register only if** a re-run of `counts` shows at least **100** readable events at each horizon (60 traded events after the price, liquidity, size and peer filters at an assumed survival of 60%, which is an assumption to be replaced by a dry run of the filters on the longer list), which means a longer event list than the store holds today. Ways to get one (all the owner's call; none done here):

1. The spin-off source's coverage before 2020-11 (UNVERIFIED whether it has any).
2. Persist Form 10-12B filing metadata (the EDGAR quarterly index is already fetched by `edgar_raw.filing_index_quarter`), or 8-K Item 2.01 via the existing `edgar.filing_events_enabled` flag: an ingest (size unestimated) that would give registrations by year back to the bar start.
3. Use `securities.known_at` (each CIK's earliest issuer-form acceptance) joined to first bars to list new CIKs since 2016 and test them against the 10-12B index: a read-only probe, not tried here.
4. A forward exam: the 26 new-issue children with ex-dates from 2024 to 2026 are a later sample; a registration after the boundary moves, disclosing this one.

## Longest window the store supports, and the boundary

Spin-off rows start 2020-11-17 and bars 2016-01-04; the window is 2020-11-01 to 2023-12-29, the [ADR 0016](../decisions/0016-development-boundary-and-forward-exams.md) recommended boundary (the store holds no `development_boundary` row yet). **No read is later than close(2023-12-29)**; rows with a later `known_at` are not read. No holdout or exam month is touched; `spinoff` has no protected window of its own.

## Prior-evidence disclosure and N

- **Not seen.** No C2 return has been computed. C1 (run 2) and C1b results are known to the author and motivated the benchmark, readings and gate here; they are a different signal on different events.
- **Family N.** `spinoff` is a new family, N = 0 before this run; this run adds 2 configurations (6 and 12 months).
- **Literature.** Cusatis, Miles and Woolridge (1993); McConnell and Ovtchinnikov (2004); McConnell, Sibley and Xu (2015); against them the Nordic 2000-2020 thesis (no effect), CSD after 2016, the QI-2 haircut. Magnitudes are UNVERIFIED in the report.
- **Survivorship.** The bar store holds almost no history for issuers delisted before 2019 and few later, so children that failed or were acquired can be missing at the entry (counted `n_dropped_no_child_bars`, `n_dropped_no_entry_bar`); the spin-off list is itself a partial third-party list. The bias direction is unknown (failed children against acquired ones). **Neither a kill nor a pass is robust to it; a kill is recorded as "killed on survivor data" and the owner decides what it retires.**
- **Count-only reads past the boundary.** The feasibility work also counted spin-off rows and first-bar dates beyond 2023-12-29 (88 rows to 2026-10-02, 26 later new-issue children) with scratch probes under `~/tradepartner-probes/c2spin/` (`probe0-4.py`); no price or return was read from them. The run itself reads nothing after close(2023-12-29).
- **Single-cutoff reads, accepted.** Shares, bars and actions are read once at close(2023-12-29) and filtered by `known_at`, as C1b did.
- **Not controlled:** industry, a child's style or leverage, parent distress.

## The dataset

`c2-spinoff-events`: one CSV, one row per known-by-boundary spin-off row (25 rows, ex-dates 2020-11-17 to 2023-12-06; columns child, parent, ex_date, ratio, known_at, kind); sha256 `7e060a03ef5f8fbae0b42040e509778dcfc8cdeb658a9766cf729a4f0ef14c15` as of 2026-10-27. The script's `frame` subcommand writes it (corporate actions and first-bar dates only; no prices). Event column `ex_date`. Split `full`, nothing sealed. The script refuses to run unless the bound file's children equal the events it rebuilds. **If the gate is met, the dataset is rebuilt and this text updated before registration.**

## Parameters

```toml experiment
slug = "c2-spinoff-children"
kind = "return"
stage = 2
title = "C2 spin-off children, size-matched: two-week entry, 6 and 12 month holds, spreads against matched peers"
confirmatory = false
provenance = "deterministic"
touches_returns = true
family = "spinoff"
claims = ["QI-2"]
seed = 20261028
splits = ["full"]

[dataset]
name = "c2-spinoff-events"

[window]
start = 2020-11-01
end = 2023-12-29

[primary]
metric = "children_12m_tight_net30_bp"
direction = "greater"
ci_level = 0.95
min_clusters = 8
inference = "per-event 12-month spread (the new-issue child's return from the open of the 10th session after its first regular-way session to the close of the 252nd session, dividend-adjusted, minus the equal-weight mean return over the same span of its 20 nearest-market-cap seasoned peers entered on the same session), minus 60 bp (30 bp per side round trip), in bp; mean over events, standard error clustered by entry month, each bound one-sided 95% (mean -/+ 1.645 SE). No threshold, so the registry verdict is n/a. Kill rules (applied in the report; every t is the smaller of the month- and quarter-clustered t; a statistic that cannot be computed is a kill; each horizon (6 and 12 months) is killed if the mean gross spread is below 60 bp or t of its net-of-30-bp mean is below 1.0 on any of four readings (cell of 100 peers, tight of 20 peers, each raw and beta-adjusted with an in-hold beta against SPY); a horizon with fewer than 30 traded events is underpowered, neither kill nor pass, and with 30 to 59 a kill is killed_low_power and retires nothing; the thesis passes only if both horizons survive). SPY is a beta regressor only and is in no benchmark"
secondary = ["12m_beta_gap", "12m_cell_gross_bp", "12m_cell_net15_bp", "12m_cell_net30_bp", "12m_cell_net50_bp", "12m_cell_sd_bp", "12m_cell_t_min", "12m_cell_t_month", "12m_cell_t_quarter", "12m_cellb_gross_bp", "12m_cellb_net15_bp", "12m_cellb_net30_bp", "12m_cellb_net50_bp", "12m_cellb_sd_bp", "12m_cellb_t_min", "12m_cellb_t_month", "12m_cellb_t_quarter", "12m_exited_early_n", "12m_logcap_gap", "12m_minus_parent_bp", "12m_minus_parent_n", "12m_n", "12m_n_quarters", "12m_raw_bp", "12m_spy_adj_bp", "12m_tight_gross_bp", "12m_tight_mde80_bp", "12m_tight_net15_bp", "12m_tight_net30_bp", "12m_tight_net50_bp", "12m_tight_sd_bp", "12m_tight_se_bp", "12m_tight_t_min", "12m_tight_t_month", "12m_tight_t_quarter", "12m_tightb_gross_bp", "12m_tightb_net15_bp", "12m_tightb_net30_bp", "12m_tightb_net50_bp", "12m_tightb_sd_bp", "12m_tightb_t_min", "12m_tightb_t_month", "12m_tightb_t_quarter", "6m_beta_gap", "6m_cell_gross_bp", "6m_cell_net15_bp", "6m_cell_net30_bp", "6m_cell_net50_bp", "6m_cell_sd_bp", "6m_cell_t_min", "6m_cell_t_month", "6m_cell_t_quarter", "6m_cellb_gross_bp", "6m_cellb_net15_bp", "6m_cellb_net30_bp", "6m_cellb_net50_bp", "6m_cellb_sd_bp", "6m_cellb_t_min", "6m_cellb_t_month", "6m_cellb_t_quarter", "6m_exited_early_n", "6m_logcap_gap", "6m_minus_parent_bp", "6m_minus_parent_n", "6m_n", "6m_n_quarters", "6m_raw_bp", "6m_spy_adj_bp", "6m_tight_gross_bp", "6m_tight_mde80_bp", "6m_tight_net15_bp", "6m_tight_net30_bp", "6m_tight_net50_bp", "6m_tight_sd_bp", "6m_tight_se_bp", "6m_tight_t_min", "6m_tight_t_month", "6m_tight_t_quarter", "6m_tightb_gross_bp", "6m_tightb_net15_bp", "6m_tightb_net30_bp", "6m_tightb_net50_bp", "6m_tightb_sd_bp", "6m_tightb_t_min", "6m_tightb_t_month", "6m_tightb_t_quarter", "kill_12m", "kill_6m", "n_dropped_horizon", "n_dropped_late_first_bar", "n_dropped_liquidity", "n_dropped_no_bars", "n_dropped_no_beta", "n_dropped_no_child_bars", "n_dropped_no_entry_bar", "n_dropped_no_mcap", "n_dropped_no_peers", "n_dropped_no_size_match", "n_dropped_no_spy", "n_dropped_preexisting", "n_dropped_price_floor", "n_dropped_size_conflict", "n_events_total", "n_new_issue", "n_securities", "n_size_ex_post", "n_size_parent_ratio", "n_traded_12m", "n_traded_6m", "off20_12m_n", "off20_12m_tight_gross_bp", "off20_12m_tight_t_min", "off20_6m_n", "off20_6m_tight_gross_bp", "off20_6m_tight_t_min", "off5_12m_n", "off5_12m_tight_gross_bp", "off5_12m_tight_t_min", "off5_6m_n", "off5_6m_tight_gross_bp", "off5_6m_tight_t_min", "status_12m", "status_6m", "verdict_code"]
comparison_set = "two configurations on one window and one event list: the 6-month and the 12-month hold of the new-issue child bought two weeks after its first regular-way session, each as a spread against size-matched same-session peers; entries +5 and +20 sessions, costs 15 and 50 bp, SPY-adjusted and parent-relative means are reported for the record only"

[multiplicity]
method = "none"

[budget]
runs = 1
configurations = 2
stop_rule = "one run, one pre-declared design: no other entry offset, horizon, peer count, size gap, universe filter, cost line or window; no selection among the reported lines. A failed or refused run is reported, not re-run in place; a second look is a new registration that discloses this one. Kill-only: the thesis is killed by its own pre-declared line only with at least 60 traded events at a horizon, and then only for effects at or above the printed 80%-power minimum detectable effect; with fewer events the result is killed_low_power or underpowered and decides nothing"
expected_effect = "spreads against matched peers of a few percentage points at 12 months (the literature's several percent a year cut by the publication haircut); a standard error of 7 to 18 percentage points on 8 to 21 events, so only a spread above roughly 10 to 20 points can separate from zero; most likely the run is underpowered, which is the finding"
```

## Expected magnitudes and red flags

- **A spread above 100 percentage points at 12 months:** suspect a missed split or a wrong child/parent pairing. Rebuild three events by hand.
- **`*_logcap_gap` beyond about 0.5, or `n_size_ex_post` near the traded count:** the matching is weak and the share count is ex-post for most events; read the spread with that in mind. A child whose share count failed on both routes is dropped; an event that fails one horizon (no beta, too few peers, exit past the boundary) keeps the other.
- **`*_beta_gap` above 0.3:** a child far from its peers' risk; a pass is unreadable.
- **`n_dropped_*` above half of the new issues:** the survivor sample dominates.

## Retirement condition

One run retires this design whatever it shows, as C1b (no re-run under this slug). What a kill retires is bounded: the effect range above the printed minimum detectable effect, not the thesis at plausible small effects. No variant (another offset, horizon, peer count, an industry match or the parent leg) is run under this slug; a later registration discloses this one and the family's N.
