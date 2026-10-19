# Experiment: Long/short spike: H1's momentum ranking as a 50/50 book with a flat borrow fee

**Kind:** return  ·  **Stage:** 2  ·  **Author:** team shortspike (backtest plan T218, #1872, #1875), for the owner  ·  **Date:** 2026-10-19 (edited 2026-10-19 on #1876, the owner's decisions, before registration)

Merging this file does not register it. After it merges, the owner registers the file, registers the dataset (the window's rebalance sessions) and opens one run with `--configurations 2`. The [research-registry spec](../specs/research-registry.md) requires that order: register, then run. The run counts in `momentum`'s N (its req 9) and is refused if it touches a protected window (its req 5). The owner runs the script against that run id. The script lives outside the repo (`~/tradepartner-probes/shortspike/long_short_spike.py`; the T218 line keeps it unmerged). The commands are in the PR that adds this file. The registry hashes the whole file, so settle the text before registering.

## Rationale

[ADR 0024](../decisions/0024-lift-long-only-and-top-1000.md) item 1 allows short positions for an idea that survives the cheap rungs. The [backtest plan](../plans/backtest.md)'s T219 to T224 build the signed engine. That build is about size L across two plans. This run is the cheapest look at the first long/short idea, made before any engine line is written. It can kill that idea. It does not gate the build: the owner builds the shorting capability whatever this run shows (2026-10-19, #1876).

**The book.** At each month-end T_i of H1's in-sample window, the script calls the public `engine.plan` with H1's registered frozen set. It reads at close(T_i), exactly as the engine does.

- **Long leg:** H1's own targets (top decile, equal weight).
- **Short leg:** the [backtest spec](../specs/backtest.md)'s amendment of 2026-10-19, items 2 and 3. k_S is the count `top_fraction` (0.10) selects from the ranked names whose raw close on T_i is at least $5. The leg is the k_S lowest-scored of those names outside the long leg, equal weight. A name below $5 is skipped and the next-lowest takes its place.
- **Returns:** each leg's return over a period comes from the adjusted closes between consecutive fill sessions F_i (the session after T_i). The marks for a period are read at close of its last session, so dividends are inside the marks. The last period ends at close(T_n) = close(2023-12-29), not at the next fill, and no read has t after that close. A delisted name is sold or covered at its last close on its final session. A name with no bar keeps its last mark. Inside a period these are the engine's exits in value.
- **The book:** 0.5 × long − 0.5 × short. Each leg is reset to half of equity at every fill. Every dollar traded on either leg pays 15 bp, H1's registered cost, exits included. The short half pays the borrow fee b / 12 a month. The free cash, 0.5 × equity, earns the store's DTB3 accrual by the engine's own rule (`riskfree`, bond-equivalent yield, the rows known at each close). The run needs the store's DTB3 rows for the window (the T170d backfill) and is opened only once they are in. A session with no rate known at its close fails the run (`RiskFreeMissing`); nothing is carried in from outside the store. The script's `--dtb3-csv` flag is **not** used for this run (research-registry req 4: market data only through the as-of API). The artifact records the source, which must read `store`.
- **Borrow lines:** b ∈ {0, 30, 300} bp a year, the [beyond-long-only report](../research/2026-10-10-beyond-long-only.md)'s grid. They are cost lines of one configuration, not configurations: the same book read at three fees.

**The control (the second configuration).** The same long leg hedged with SPY: 0.5 × long − 0.5 × SPY, at the same fees and the same b. SPY is the engine's benchmark security (by symbol, #840), marked on the adjusted frame. The control answers the B12 question: does a stock short leg add anything over an index hedge of the same long leg?

**What it decides (pre-declared; kill-only).** One number decides: the **net spread**. That is the monthly long-minus-short spread net of 15 bp per side on both legs and of 30 bp a year borrow on the short leg, before the T-bill interest on free cash. It is computed as 2 × (the 50/50 book's return at 30 bp − 0.5 × the T-bill return), its mean times 12, in pp a year (`primary.metric`). Each bound is one-sided 95%. **The run kills the long/short `momentum` idea if** the net spread's point estimate is below 0, **or** its upper bound is below +2 pp/yr. A kill means no long/short `momentum` hypothesis file is written on this ranking. **Otherwise it records the result and gates nothing.** It does not park T219 onward, and a survivor here is not a survivor of the kill ladder. The build waits only for this run to exist, because the owner builds the shorting capability regardless (2026-10-19, #1876). The registry's verdict is `n/a` (no `primary.threshold`): the kill rule reads the point estimate and the upper bound, which a single threshold cannot express. The beyond-long-only report's B12 park rule (the gross spread's lower bound above +2 pp/yr; `book_minus_hedged_cagr_b30` above 0) and the gross spread are reported beside it, for the record only.

**The engine check (not part of the registered result).** The signed engine rests on one assumption (spec item 5): a short marked on the dividend-adjusted frame differs from paying the dividend in lieu from cash only by a second-order term. The script's `engine-check` subcommand tests this on the fixture store. It runs a hand two-name 50/50 book across a fixture dividend, once marked on the adjusted frame and once paying the dividend from cash. It reads no real data and opens no run. The agent ran it on 2026-10-19, before this file was merged: short SEC_SPY across its $1.35 dividend of 2018-06-25 (prior close $239.52), long SEC_MTUM, half of equity on each leg. **The two books are not identical at the ex-date close.** They differ by −4.4 × 10⁻⁶ of equity there, about 0.2% of the dividend paid. Over the six sessions from the ex-date, the difference equals n·D·(1 − P(s) / (P(X−1) − D)) to 1.6 × 10⁻¹⁶. Here n is the shares short, D the dividend, P(s) the raw close and X the ex-date. That is the store's factor convention (1 − D / prior close). The adjusted mark re-shorts the dividend at the prior close less the dividend, not at the ex-date close. So the second-order term runs from that price, and it is already non-zero at the ex-date close. Spec item 5 and the T218, T220 and T222 lines now state this form (#1876). The term is as small as the spec says. The T218 report states the check.

## Prior-evidence disclosure

- **The long leg has been seen.** It is H1's own book over its own in-sample window: trials 1 and 2 in the registry. H1 trailed SPY by 4.1 pp/yr in-sample, with beta 1.04 and alpha −4.5 pp/yr ([B11](../research/2026-10-10-b11-beta-alpha-split.md)). The six T114 variants show alpha between −1.5 and −4.9 pp/yr.
- **A SPY hedge of the long leg has been seen, in another form.** B11 shorted the trailing beta in SPY (not 0.5 × SPY) and left every in-sample book 8 to 17 pp/yr below T-bills, from April 2021 to December 2023. So the control in this run is expected to trail T-bills, and the question is only whether the stock short leg does better.
- **The short leg has not been seen.** No short leg of any ranking has been computed on the store. Nobody has read the bottom decile's returns, the equal-weighted universe's returns or the $5 floor's counts on this window.
- **The holdout is spent.** `momentum`'s holdout [2024-01-01, 2026-09-30] was spent by H1's trial 4. This run stops at 2023-12-29 and reads nothing after close of that day.
- **Literature:** LS-3 (NOT SUPPORTED: in large caps, single-stock shorts add no net value over an index hedge of the long leg, post-2006, net of trading and borrow; Israel and Moskowitz conflict on momentum gross). The beyond-long-only report's prior for a dollar-neutral momentum composite was about −2 pp/yr against T-bills (range −7 to +3).

## The dataset

`long-short-spike-sessions`: one CSV with one column, `session`. It has one row per month-end rebalance session of the window, 41 rows from 2020-08-31 to 2023-12-29, as `schedule.rebalance_sessions` gives them. The script's `frame` subcommand writes it. It is the run's frame, not its data: the market data come from the store through the as-of API (req 4), and the script refuses to run if the bound file's sessions are not the window's rebalances. The split is `full`. Nothing is sealed, because the protected window is the family's holdout, which the run's window ends before.

## Parameters

```toml experiment
slug = "long-short-spike"
kind = "return"
stage = 2
title = "Long/short spike: H1's momentum ranking as a 50/50 book with a flat borrow fee"
confirmatory = false
provenance = "deterministic"
touches_returns = true
family = "momentum"
claims = ["LS-3"]
hypothesis_ref = "h1-momentum-12-1"
seed = 20261019
splits = ["full"]

[dataset]
name = "long-short-spike-sessions"

[window]
start = 2020-08-31
end = 2023-12-29

[primary]
metric = "net_spread_b30_pp_per_year"
direction = "greater"
ci_level = 0.95
min_clusters = 40
inference = "the monthly net spread 2 x (r_book at 15 bp per side and 30 bp/yr borrow - 0.5 x rf), that is r_long - r_short net of trading and borrow and before cash interest; its mean times 12 in percentage points a year; each bound one-sided 95% (mean -/+ 1.645 Newey-West standard errors, 3 lags); clusters are the 40 monthly periods. No threshold, so the registry verdict is n/a; the kill rule (point estimate below 0, or ci_high below +2.0) is applied in the report"
secondary = ["gross_spread_pp_per_year", "gross_spread_ci_low", "book_cagr_b0", "book_cagr_b30", "book_cagr_b300", "book_excess_rf_b0", "book_excess_rf_b30", "book_excess_rf_b300", "book_vol_b30", "hedged_cagr_b0", "hedged_cagr_b30", "hedged_cagr_b300", "hedged_excess_rf_b0", "hedged_excess_rf_b30", "hedged_excess_rf_b300", "hedged_vol_b30", "book_minus_hedged_cagr_b0", "book_minus_hedged_cagr_b30", "book_minus_hedged_cagr_b300", "book_cagr_gross", "long_leg_cagr_gross", "short_leg_cagr_gross", "spy_cagr", "ew_universe_cagr", "short_minus_ew_universe_cagr", "rf_cagr", "short_leg_worst_month", "max_short_weight", "months_short_weight_above_cover", "floor_replaced_share", "n_delisted_short", "n_periods"]
comparison_set = "two configurations on one window: the 50/50 stock book (0.5 x H1's long leg - 0.5 x the bottom-decile short leg) against its control (0.5 x the same long leg - 0.5 x SPY), each net of 15 bp per side and at borrow b in {0, 30, 300} bp/yr (cost lines, not configurations), both against T-bills (DTB3); the short leg also against the equal-weighted universe (B11's size-tilt point). Reported per period in the artifact: both legs, SPY, the universe and the T-bill return"

[multiplicity]
method = "none"

[budget]
runs = 1
configurations = 2
stop_rule = "one run, one pre-declared design: no other ranking, weighting, floor, fee, hedge ratio or window; no DSR and no selection. A failed or refused run is reported, not re-run in place; a second look is a new registration that discloses this one. Kill-only: the long/short momentum idea is killed if the net spread at 30 bp is below 0 or its one-sided 95% upper bound is below +2.0 pp/yr; otherwise the result is recorded and gates nothing (T219 onward wait only for the run to exist; owner, 2026-10-19, #1876)"
expected_effect = "a gross spread near zero to a few pp a year with a wide interval (40 months; a monthly spread volatility of 3 to 5% puts the one-sided 95% half-width (1.645 x 12 x sigma / sqrt(40)) near 9 to 16 pp/yr, so a kill by the upper bound needs a mean below about -7 to -14 pp/yr, and the point-estimate rule decides most outcomes); the book below T-bills net of costs at 30 bp (the beyond-long-only prior: about -2 pp/yr, range -7 to +3); the SPY-hedged control below T-bills (B11: the hedged long leg 8 to 17 pp/yr below); the $5 floor replacing few names in a top-1000 universe"
```

## Expected magnitudes and red flags

- **A gross spread above 20 pp/yr, or a short leg falling more than 30% a year:** suspect look-ahead (a read after close(T_i) for a plan, or after a period's end for its marks) or a sign error. Check the artifact's `max_read_t` against close(2023-12-29), and rebuild one month's legs by hand.
- **`floor_replaced_share` above 0.2:** the universe's own $5 rule (ADR 0006) should already remove most of them. A large share means the raw-close read is not the T_i bar.
- **`max_short_weight` above 0.10 in many months:** the drift cover in spec item 7 (`portfolio.short_cover_weight = 0.10`) would fire often. That is a finding for open question 22, not a bug, unless a single name jumps by an implausible factor (a split missed by the adjusted frame).
- **`n_delisted_short` above 10, or a delisted short with a large gain at its last close:** read its listing end. Covering at the last close is conservative for a delisting for cause, so a large short gain there is not that rule's doing.
- **The control's CAGR far from B11's hedged line:** B11 hedged by trailing beta, this run hedges at 0.5. A gap of a few pp/yr is expected; a different sign of the excess over T-bills is a flag.
- **`spy_cagr` far from 11.1%:** that is SPY's stored total-return CAGR over 2020-08-31..2023-12-29 in B11. This run's SPY periods start at F_0 and end at T_n, so it should be close, not equal.
- **`rf_cagr` outside about 1.5% to 2.5%:** DTB3 averaged about 2% over the window (near zero to early 2022, about 5% in 2023).
- **The engine check:** it ran on the fixture store before registration (above). The spec's wording needs one fix (#1876), and the term is second order. This run's dividends sit inside the adjusted marks, as the engine's will. So the check changes nothing in this run.

## Retirement condition

One run retires this design whatever it shows. The kill rule above is applied once, in the report. A kill retires the long/short `momentum` idea on this ranking; it does not stop the signed-engine build. No variant of this book is run under this slug. A later long/short `momentum` hypothesis file discloses this run in its preamble, and B12, if run, remains the full measurement.
