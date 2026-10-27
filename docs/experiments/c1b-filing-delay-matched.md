# Experiment: C1b filing delay, size-matched: causal terciles, spreads against matched peers

**Kind:** return  ·  **Stage:** 2  ·  **Author:** team c1lag (issue #2371), for the owner  ·  **Date:** 2026-10-27

Merging this file does not register it. After it merges, the owner registers the file, registers the dataset (the filings' event list) and opens one run with `--configurations 2`, in the order the [research-registry spec](../specs/research-registry.md) requires. The script lives outside the repo (`~/tradepartner-probes/c1lag/c1b_filing_delay.py`). The registry hashes the whole file, so settle the text first. **No C1b return has been computed when this text was written.** The only reads are the counts in the feasibility section.

## Why C1b exists (what C1 run 2 showed)

[C1](c1-filing-delay.md) (run 2, `filing_delay`) killed the early-filer long (-79 bp gross against SPY at 60 sessions, t -2.1) and let the late-filer short "survive" (+98 bp net, t 1.37). But all three terciles lagged SPY (early -79, normal -148, late -165 bp), the early-minus-late spread was +86 bp at t 0.98, and the causal read reversed. So that result was size and market beta, not filing delay. Two design flaws caused it, and this file fixes both:

1. **The benchmark.** SPY is cap-weighted large caps; the filers here are mostly small. Every group looked bad against it, and the kill lines were absolute (against SPY), not spreads.
2. **The tercile cutoffs.** C1's cutoffs used every filing of the quarter, so an event's label depended on later filings.

C1b measures **only spreads against size-matched, time-aligned peers**, with **causal cutoffs as the registered rule**. SPY is never a benchmark; its only role is as the regressor of a pre-entry beta.

## The design

**Events, lag change, entry, outcome** are C1's, unchanged (so the two are comparable): per 10-Q or 10-K, `lag` = XNYS sessions from period end to the acceptance date (America/New_York); `d` = lag minus the lag of the same CIK's same-form filing whose period end is 350 to 380 days earlier (nearest 365; ties to the lexically smaller accession; only candidates accepted before the event); one security per CIK (the lowest `security_id`); entry at the open of the first session whose date is later than the acceptance date; exit at the close of the 20th or 60th session counting the entry session as the first; dividend-adjusted frame; a name with no bar after its last bar is held at its last close (delisting rule in value); per-side costs 15 and 30 bp on the stock leg. Tradability filters as C1: raw close of the prior session at least $5, median dollar volume of the 20 prior sessions at least $1,000,000 over at least 10 bars.

**(4) Causal terciles, the registered rule.** The cutoffs for an event accepted in calendar quarter Q and form F are the 1/3 and 2/3 linear quantiles of `d` over form-F events accepted in the four calendar quarters Q-4 to Q-1, all complete before Q begins. **Early:** `d` strictly below the lower cutoff. **Late:** strictly above the upper. **Normal:** the rest. A pool under 200 events leaves the event unlabelled and untraded. Nothing after the event's own acceptance enters its label. The events before 2016-07-01 only feed cutoffs and are not traded (the bar store starts 2016-01-04 and a beta needs history).

**(1) The benchmark: matched peers.** For each early or late event:

- **Size bucket.** The event's market cap at entry is its shares outstanding times its raw close of the session before entry. Shares are the company-level (`class_member` empty) `shares_outstanding` fact read through `facts_as_of`, the newest row with `known_at` at or before the acceptance and an as-of date within 400 days. An event with none is dropped and counted. Buckets are the terciles of market cap within the event's (calendar quarter, form) over the tradable events; a group under 30 events is dropped and counted. Size is a matching variable and not a signal. The cutoffs are an **ex-post matching cell** (the quarter's caps include entries up to three months after the event, so they are not causal); an event and its peers share them, and they read no outcome.
- **Peers.** The `normal` events (causal rule) of the same form and size bucket whose entry session is within 5 sessions of the event's, the event itself and any other event of the same CIK excluded. Each peer's return runs over its own entry-to-exit span at the same horizon. The peer benchmark is their equal-weight mean. Fewer than 5 peers: the event is dropped and counted.
- **Spread.** `spread = event return - peer mean`, per horizon. **Why this benchmark:** it removes what C1 could not. The market's level and the average size premium of a cell cancel, because the peers are the same size tercile, the same form, entered within a week, held the same number of sessions, and carry the same filing-day reaction at their own start. What is left is the difference between an early or late filer and a typical-lag filer of its own cell.
- **Beta adjustment (the second spread).** Matching cancels the market only if event and peers have the same beta; a beta gap times SPY's average 60-session return could by itself clear a 60 bp bar. So every traded event also gets a causal beta: the OLS slope of its daily dividend-adjusted returns on SPY's over the up to 120 sessions ending the session before entry, at least 60 return pairs (else dropped and counted, `n_dropped_no_beta_*`). `spread_badj = (r_i - beta_i x spy_i) - mean over peers of (r_p - beta_p x spy_p)`, over the same spans. SPY is used here only as a beta regressor and never as a benchmark. **Every kill line must hold on both the raw and the beta-adjusted spread, or it fires.** The kill-only structure makes that strictly safe. `early_60_beta_gap` and `late_60_beta_gap` (event minus peers) show how much beta the matching left.
- **Size-tight spreads (the third and fourth).** Terciles are coarse, so each event also gets a tight reading: its peers are the 10 `normal` events of its cell and entry window that are nearest to it in log market cap (the 5-peer minimum already applies). `spread_sz = r_i - mean(r of those 10)` and its beta-adjusted twin `spread_szb`. **The four readings are raw, beta-adjusted, size-tight and size-tight beta-adjusted; every kill line must hold on all four, or it fires.** **Why not the alternatives:** an equal-weight index of all filers in the quarter ignores size; a Fama-French style factor model needs factor data the store lacks; a size-matched ETF is not in the store. The normal-lag group is the cell's own control, with the same selection into "files on time".

**What it does not remove, stated.** Size terciles are coarse (a tercile can span a tenfold range of market cap), so a group can still be smaller or larger than its peers on the raw and beta-adjusted readings; the size-tight readings cut that, and `early_60_logmcap_gap` and `late_60_logmcap_gap` (mean log cap, event minus peers, on the terciles' peers) report how much was left. A late filer can also be cheaper or weaker than its peers on style. SEC filer status (accelerated filers file sooner, and status follows past price) may tilt early filers to past winners. Neither is controlled here. `early_60_raw_bp` and `late_60_raw_bp` (raw returns) and `*_spy_adj_bp` (against SPY, for the record only) are reported so the owner can see how much the matching moved the answer.

## Kill lines (pre-declared, 60 sessions; 20 reported)

All on the **spread**, net of the stated costs. Every t is **the smaller of the month-clustered and quarter-clustered t** of the event-level spread (clusters by acceptance month and by acceptance calendar quarter; no small-sample correction). A statistic that cannot be computed (no events, or a clustered t that is undefined in either clustering) counts as a kill.

- **Long thesis (early minus matched peers; the primary).** Killed if the mean gross spread is **below 60 bp per event** (the 30 bp per side round trip, so the net-of-30 bp spread is below 0), **or** t of the net-of-30 bp spread is **below 1.0**, **on each of the four readings** (any failing kills). These are the owner's bars of 2026-10-27, kept.
- **Short thesis (late minus matched peers).** The short loses the spread: it earns minus the late-minus-peer spread, pays 30 bp per side and 30 bp a year borrow pro rata for 60/252 of a year. Killed if the mean of that net return is **below +60 bp per event**, **or** its t is **below 1.0**, on each of the four readings (any failing kills). The owner's bar of 2026-10-27, kept. It is deliberately stricter than the long's (about 125 bp gross): the short is paper-only, its borrow is the least certain number, and a short that merely breaks even is not worth an engine.
- **Idealisation, stated.** The matched-peer leg of each spread is taken as costless to trade. That flatters both lines. Because the lines kill and do not approve, the flattery makes a kill stronger and a pass weaker; a pass is an upper bound.
- **Reported, not decisive:** 20-session spreads, the 15 bp lines, borrow lines 0 and 300 bp a year (a short that passes at 30 bp a year and fails at 300 is stated as such in the report), raw and SPY-adjusted means, per-group counts.
- **A pass's t is an upper bound:** 60-session holds overlap neighbouring months' clusters, so the clustered standard errors are somewhat understated.

**Shorts are paper-only until the signed engine exists** ([ADR 0024](../decisions/0024-lift-long-only-and-top-1000.md)); anomaly short legs often net about zero after borrow (LS-6), there are no fractional shorts at $100 (LS-16), and small late filers are often hard to borrow, so the 300 bp line is the more honest one.

**What pass and kill mean.** Pass: the thesis's kill line did not fire. It goes to rung 3 (capacity and fills at $100) of the [ADR 0023](../decisions/0023-break-first-fast-books-paper-exam.md) ladder, then rung 4; it is not a strategy. Kill: recorded as "killed on survivor data" (below), and the owner decides whether it retires the thesis.

## (3) The longest window the store supports, and the boundary

Bars start 2016-01-04, so traded events start 2016-07-01 (a 120-session beta history with at least 60 return pairs, and four quarters of causal-cutoff history before it). The last traded event is accepted 2023-09-29 so every 60-session exit is read by 2023-12-29. That date is the [ADR 0016](../decisions/0016-development-boundary-and-forward-exams.md) recommended development boundary: the store holds no `development_boundary` row yet, and this file keeps to the recommended value anyway. **The registered window is 2015-04-01 to 2023-12-29. No read is later than close(2023-12-29).** Momentum's and profitability's exam months (2024-01-01 to 2026-09-30) are not touched; `filing_delay` has no protected window of its own. The 2020 crash and the 2022 drawdown are inside the window; spreads against same-window peers absorb them.

## Feasibility read (counts only, no returns)

Read-only DuckDB connection to the owner's store, 2026-10-27, before this file was written. It counts filings and security ids with bars, and reads no price, return or volume value. Year is the America/New_York acceptance year; "with prior" has a same-form, same-CIK filing 350 to 380 days earlier accepted earlier; "with bars" adds a security with a bar on or before the acceptance date and one 90 or more days after; "with shares" adds a company-level `shares_outstanding` fact known at the acceptance and no more than 400 days old.

| Year | 10-Q and 10-K accessions | with prior | with bars | with bars and shares |
|---|---|---|---|---|
| 2015 | 27,138 | 24,402 | 0 | 0 |
| 2016 | 25,376 | 23,355 | 11,608 | 10,618 |
| 2017 | 24,357 | 22,415 | 12,318 | 11,224 |
| 2018 | 23,607 | 21,767 | 13,157 | 11,941 |
| 2019 | 22,974 | 21,185 | 14,099 | 12,737 |
| 2020 | 22,410 | 20,711 | 14,734 | 13,140 |
| 2021 | 25,060 | 20,980 | 15,226 | 13,338 |
| 2022 | 26,184 | 22,905 | 17,009 | 13,870 |
| 2023 | 24,603 | 22,963 | 17,178 | 14,027 |

Filings reach back to 2009 in the store, but bars begin 2016-01-04, which is what bounds the window; 2015's rows are the cutoff history only. The total with bars and shares over 2016 to 2023 is 100,895 across all three terciles. That is a calendar-year count: it includes January to June 2016 (not traded) and October to December 2023 (outside the dataset), and the "bar 90 or more days after" test looked at whether bars exist in 2024, an existence check only that read no price. The price, liquidity, beta, peer and size-group filters come on top; the run counts them. The events without a security with bars (between a third and a half of those with a prior) are issuers the bar store does not carry, as in C1.

## Prior-evidence disclosure and N

- **C1 run 2 has been seen.** Its result, stated above, motivated every change here: a different benchmark, causal cutoffs, a longer window. This is a second look at the same idea, disclosed as one. C1's 2017 to 2019 events are inside this window, so the two are not independent samples.
- **The family's N now includes C1 run 2.** `filing_delay`'s research-run count is C1 run 2's 2 configurations; this run adds 2, to 4 (`n_configurations = 2`, the early-minus-matched long and the late-minus-matched short). Family N is not reset by a better design.
- **Literature** is as in C1: Bartov and Konchitchki (2017), Johnson and So (2018), Dai et al. (2021); against them the RavenPack decay note, the McLean and Pontiff haircut (QI-2) and LS-6 for the short leg.
- **Survivorship.** The bar store holds almost no history for issuers that delisted in 2017 to 2019 (4 of 1,301 CIKs that filed in 2017 and stopped by mid-2019 have any bars), and the same is likely later. Those events drop at entry; `n_dropped_no_entry_bar_*` counts them by group. The bias direction is unknown (failures against merger-target premiums), so **neither a kill nor a pass is robust to it. A kill is recorded as "killed on survivor data"** and the owner decides whether it retires the thesis.
- **Single-cutoff reads, accepted.** Shares, bars and the price and volume filters are read once at close(2023-12-29), not at each entry (as C1 did). `facts_as_of` returns the latest revision of a share count, so a count restated after the acceptance hides the value known at the time; that event then drops or falls back to an older row, a selection on later information. The effect is small and applies equally to every group; and the successor-chain re-keying of a predecessor's bars has no live chain in the store today.
- **Not controlled:** filer status and within-bucket style and momentum (above).

## The dataset

`c1b-filing-delay-matched-events`: one CSV, one row per event, 189,282 rows, from 2015-04-01 to 2023-09-29 (accession, security id, CIK, form, acceptance instant UTC, acceptance date, period end, the prior filing's accession, acceptance and period end, both lags and `d`). The script's `frame` subcommand writes it from the store (filings only, no prices). Event column `acc_date`. Split `full`, nothing sealed. The script refuses to run unless the bound file's accessions equal the events it rebuilds from the store.

## Parameters

```toml experiment
slug = "c1b-filing-delay-matched"
kind = "return"
stage = 2
title = "C1b filing delay, size-matched: causal terciles, spreads against matched peers"
confirmatory = false
provenance = "deterministic"
touches_returns = true
family = "filing_delay"
claims = ["QI-2", "LS-6"]
seed = 20261028
splits = ["full"]

[dataset]
name = "c1b-filing-delay-matched-events"

[window]
start = 2015-04-01
end = 2023-12-29

[primary]
metric = "early_minus_matched_60_net30_bp"
direction = "greater"
ci_level = 0.95
min_clusters = 60
inference = "per-event 60-session spread (the early-tercile event's return from the next session open after acceptance to the close of the 60th session, dividend-adjusted, minus the equal-weight mean return over each peer's own entry-to-exit span of the same-form, same-size-tercile normal-lag events entered within 5 sessions), minus 60 bp (30 bp per side round trip), in bp; terciles are causal (cutoffs from the same form's previous four calendar quarters); mean over events, standard error clustered by acceptance month, each bound one-sided 95% (mean -/+ 1.645 SE). No threshold, so the registry verdict is n/a. Kill rules (applied in the report; every t is the smaller of the month- and quarter-clustered t; a statistic that cannot be computed is a kill; each rule must hold on four readings (raw, beta-adjusted with a causal 120-session pre-entry beta, size-tight on the 10 nearest-cap peers, and both) or it fires): long if the mean gross early-minus-matched spread is below 60 bp or t of its net-of-30-bp mean is below 1.0; short if the late tercile short's mean 60-session net return against the matched peers, at 30 bp per side and 30 bp/yr borrow, is below +60 bp or its t is below 1.0. SPY is a beta regressor only and is in no benchmark"
secondary = ["early_20_n", "early_20_net15_bp", "early_20_net30_bp", "early_20_raw_bp", "early_20_spread_gross_bp", "early_20_spy_adj_bp", "early_20_t_min", "early_20_t_month", "early_20_t_quarter", "early_60_badj_gross_bp", "early_60_badj_t_min", "early_60_beta_gap", "early_60_logmcap_gap", "early_60_n", "early_60_net15_bp", "early_60_net30_bp", "early_60_raw_bp", "early_60_spread_gross_bp", "early_60_spy_adj_bp", "early_60_sz_gross_bp", "early_60_sz_t_min", "early_60_szb_gross_bp", "early_60_szb_t_min", "early_60_t_min", "early_60_t_month", "early_60_t_quarter", "kill_long", "kill_short", "late_20_n", "late_20_raw_bp", "late_20_spread_gross_bp", "late_20_spy_adj_bp", "late_60_badj_gross_bp", "late_60_beta_gap", "late_60_logmcap_gap", "late_60_n", "late_60_raw_bp", "late_60_spread_gross_bp", "late_60_spy_adj_bp", "late_60_sz_gross_bp", "late_60_szb_gross_bp", "n_delisted_exits", "n_dropped_horizon_early", "n_dropped_horizon_late", "n_dropped_horizon_normal", "n_dropped_liquidity_early", "n_dropped_liquidity_late", "n_dropped_liquidity_normal", "n_dropped_no_beta_early", "n_dropped_no_beta_late", "n_dropped_no_beta_normal", "n_dropped_no_entry_bar_early", "n_dropped_no_entry_bar_late", "n_dropped_no_entry_bar_normal", "n_dropped_no_mcap_early", "n_dropped_no_mcap_late", "n_dropped_no_mcap_normal", "n_dropped_no_peers_early", "n_dropped_no_peers_late", "n_dropped_no_peers_normal", "n_dropped_price_floor_early", "n_dropped_price_floor_late", "n_dropped_price_floor_normal", "n_dropped_small_size_group_early", "n_dropped_small_size_group_late", "n_dropped_small_size_group_normal", "n_events_early", "n_events_late", "n_events_normal", "n_events_total", "n_no_label", "n_securities", "n_traded_early", "n_traded_late", "n_traded_normal", "short_late_20_c15_b0_net_bp", "short_late_20_c15_b300_net_bp", "short_late_20_c15_b30_net_bp", "short_late_20_c30_b0_net_bp", "short_late_20_c30_b300_net_bp", "short_late_20_c30_b30_net_bp", "short_late_20_t_min", "short_late_20_t_month", "short_late_20_t_quarter", "short_late_60_badj_c30_b30_net_bp", "short_late_60_badj_t_min", "short_late_60_c15_b0_net_bp", "short_late_60_c15_b300_net_bp", "short_late_60_c15_b30_net_bp", "short_late_60_c30_b0_net_bp", "short_late_60_c30_b300_net_bp", "short_late_60_c30_b30_net_bp", "short_late_60_sz_c30_b30_net_bp", "short_late_60_sz_t_min", "short_late_60_szb_c30_b30_net_bp", "short_late_60_szb_t_min", "short_late_60_t_min", "short_late_60_t_month", "short_late_60_t_quarter"]
comparison_set = "two configurations on one window and one event list: the early-filer long and the late-filer short, each as a spread against size-matched, time-aligned normal-lag peers, at 20 and 60 sessions and 15 and 30 bp per side, borrow lines 0, 30 and 300 bp/yr for the short; raw and SPY-adjusted means are reported for the record only"

[multiplicity]
method = "none"

[budget]
runs = 1
configurations = 2
stop_rule = "one run, one pre-declared design: no other horizon, lag definition, cutoff rule, size bucket count, peer window, universe filter, cost line or window; no selection among the reported lines. A failed or refused run is reported, not re-run in place; a second look is a new registration that discloses this one and C1 run 2. Kill-only: each thesis is killed by its own pre-declared line; a pass sends it to rung 3, not to trading"
expected_effect = "spreads against matched peers near zero to a few tens of bp per event at 60 sessions (C1's published-number estimate was about 55 bp for the early long, below the 60 bp round trip); the late short near zero or negative net of borrow (LS-6); a clustered standard error near 10 to 25 bp over tens of thousands of traded early and late events across 8 years, so only a spread above roughly 40 to 60 bp can separate from zero; most likely both lines kill, which is the finding"
```

## Expected magnitudes and red flags

- **A spread above 300 bp gross:** suspect look-ahead (an entry on the acceptance session, a label that used a later filing) or a split missed by the adjusted frame. Check the artifact's events against close(2023-12-29) and rebuild three events by hand.
- **`early_60_raw_bp` and `late_60_raw_bp` both far below zero while the spreads are near zero:** the matching worked and the earlier C1 result was beta and size.
- **`n_dropped_no_peers_*` or `n_dropped_small_size_group_*` above a fifth of a group's events:** the matched cell is thin; read before trusting the spread.
- **`early_minus_matched` and `late_minus_matched` with the same sign:** a within-bucket size effect, not a timing effect.
- **A very different count of early and late events:** the causal cutoffs lag a trend in `d` (lags drifting up or down across years). Expected to some degree; the labels are still causal.

## Retirement condition

One run retires this design whatever it shows. The kill rules are applied once, in the report, as "killed on survivor data" where they fire; the owner decides what a kill retires. No variant (another horizon, bucket count, peer window, an NT-form signal or the absence entry) is run under this slug; a later registration discloses this one, C1 run 2 and the family's N.
