# Experiment: C1c filing delay on Sharadar bars: does adding delisted filers change C1b's verdict?

**Kind:** return  ·  **Stage:** 2  ·  **Author:** team sharadarc1c (issue #2417), for the owner  ·  **Date:** 2026-10-27

Merging this file does not register it, and it does not run it. After it merges, the owner registers the file, registers the dataset (the same event list as C1b) and opens one run with `--configurations 2`, in the order the [research-registry spec](../specs/research-registry.md) requires. The script lives outside the repo (`~/tradepartner-probes/c1lag/c1c_filing_delay.py`). The registry hashes the whole file, so settle the text first. **No C1c return has been computed when this text was written.** The only reads are the event counts below and a price-adjustment audit that computes no event return.

## Why C1c exists

[C1b](c1b-filing-delay-matched.md) (run 3) was killed on both theses and recorded "killed on survivor data" (claim TP-8): the bar store holds almost no history for filers that delisted, so those events dropped at entry (`n_dropped_no_entry_bar_*`), and the file said neither a kill nor a pass is robust to that. The question here is narrow: **does including delisted filers change C1b's verdict?** The owner has one paid month of Sharadar Prices (SEP prices and ACTIONS, with delisted names), kept outside the store under the [ADR 0022 amendment of 2026-10-27](../decisions/0022-no-vendor-security-master.md#amendment-2026-10-27-2417-sharadar-prices-as-a-research-only-side-dataset-for-one-check). This is the one experiment that amendment allows to read it.

## The design: C1b's, with one change

**Everything is C1b's, unchanged, and the script imports C1b's code for it** (`c1b_filing_delay.py`: the event list and its dataset, `lag` and `d`, the causal terciles, the entry rule, the 20- and 60-session horizons, the tradability filters, the size buckets, the matched-peer rule, the beta adjustment, the four readings, the costs, the borrow lines, the clustered statistics and the kill lines). The event list is the same export (the same bytes, so `dataset register` returns the existing version); the labels are computed on all filings, with no regard to bars. **Same peers rule:** `normal` events of the same form and size bucket, entry within 5 sessions, at least 5 peers, the event's own CIK excluded, the 10-nearest-cap peers for the size-tight readings. **Same window:** events accepted 2015-04-01 to 2023-09-29, traded from 2016-07-01, no read later than close(2023-12-29) (the ADR 0016 recommended boundary).

**The one change is where the bars come from.** For each event, C1b read the store's adjusted and raw bars. C1c reads Sharadar SEP and ACTIONS, so a filer that later delisted has bars up to its last trading day. Shares outstanding (for market cap), the event list, the trading calendar and SPY (a beta regressor only, never a benchmark) still come from the store through the as-of API, exactly as in C1b.

### The crosswalk

CIK to Sharadar `permaticker`, never by ticker. The CIK of an event is matched to the CIK parsed from TICKERS `secfilings` (10 digits). Candidates are TICKERS rows with table SEP and category `Domestic Common Stock Primary Class`, or, if the CIK has none, `Domestic Common Stock`; secondary classes are never used. If a CIK has several candidates, the one whose first-to-last price span covers most days of [2016-01-04, 2023-12-29], ties to the lowest numeric permaticker (the number of events whose CIK has several candidates is recorded in the run's artifact and in the private counts; the other candidates' spans are not used, and the secondary `n_outside_chosen_span` counts events whose acceptance date falls outside the chosen span while another candidate's span covers it). An event whose CIK has no candidate is dropped and counted (`n_unmapped_*`). Bars of a permaticker are the SEP rows of its current TICKERS `ticker`, which Sharadar keeps rewired through ticker changes. This is the method of the 2026-10-11 overlap test ([report](../research/2026-10-11-sharadar-overlap-test.md), method `METHOD-prereg.md`, in the owner's library) applied to the event CIKs; the earlier crosswalk file covered only universe members, so the event-CIK crosswalk is rebuilt from the 2026-10-27 TICKERS file. **Known gap, stated:** TICKERS `secfilings` holds the current CIK only, so a filer that changed CIK misses its earlier filings; this drops those events (counted) and is not corrected.

### Bars and corporate actions

From SEP and ACTIONS only, rebuilt rather than taken from the vendor's adjusted column:

- **Raw prices:** `closeunadj` is the raw close. SEP `open` and `volume` are split-adjusted to today, so the raw open is `open x closeunadj / close` and the raw volume is `volume x close / closeunadj` (verified on a known split: the 2020 four-for-one).
- **Adjusted frame (C1b's "dividend-adjusted frame"):** going backward from the last bar, a session before an ex-date is scaled by the event's factor. A split with ACTIONS value r (new shares per old) scales by 1/r. A cash `dividend` or `spinoffdividend` of D scales by (prev raw close - D) / prev raw close. Sharadar lists dividends on today's share basis (including splits dated after the window), so D is first converted to the basis of the bar before the ex-date by multiplying by that bar's SEP `closeunadj / close`, which holds every later split. Bars with zero volume are dropped, as C1b's traded-only reads do. A dividend whose result is not positive is skipped and counted (`n_bad_div`). An ex-date that is not a bar applies at the next bar. Actions dated after close(2023-12-29) are never read.
- **Audit, before registration, no event return:** the script's `adjust-check` compares this adjustment with the vendor's `closeadj` on a seeded sample of 400 names (seed 20261028); the result is in the private audit file below. It is a data audit, not a kill line. **Bar, set now:** at least 90% of the sampled names must have a ratio of the script's adjusted close to `closeadj` that stays within 2% over the window (a name outside it is one where the vendor adjusted something ACTIONS does not list, or rounded a sub-dollar price). The pinned audit meets it; this is a statement about the script's rebuild, not about the vendor's data. A rerun of the audit on a changed script that falls below the bar stops the run.

### Delisting returns

**As C1b states:** a name with no bar after its last bar is held at its last close through the exit session, so a delisting adds no return of its own. That is a known idealisation and it is kept for comparability, with its direction stated: a bankruptcy or regulatory delisting often loses value after the last print (Sharadar's last bar is the last recorded trade, and many such names trade over the counter before it), so the rule flatters a long held through one and under-states the gain of a short; a cash acquisition is roughly right. Nothing is imputed. Three reported readings, none decisive: `n_delisted_exits` (events whose span ended on the last bar), and the same count by type: an early exit is `gap` if the name's final bar lies after the exit session (a hole in the bars, never read as a delisting), else the ACTIONS delisting type dated within 5 sessions either side of the final bar (`bankruptcyliquidation`, `regulatorydelisting`, `voluntarydelisting`, `acquisitionby`, else `unclassified`), written to the run's exploratory field; and a **delisting-return stress** for both legs: the raw-reading 60-session net means (30 bp per side, 30 bp a year borrow for the short) beside the same means with every event that exited on a bankruptcy or regulatory-delisting last bar re-marked a further 30% lower from its last close. The stress decides nothing; it shows how much of a long's survival, or a short's, rests on the hold-at-last-close idealisation. ACTIONS rows dated after close(2023-12-29) are not read for it. A stricter rule (for example a fixed loss on bankruptcy) is not part of this design; if the result turns on delisted names, that is the follow-up to register.

## Kill lines and how the verdict is read

The kill lines are C1b's, word for word, on the primary reading **A** (every event with a Sharadar crosswalk, delisted names included): long (early minus matched peers) is killed if the mean gross spread at 60 sessions is below 60 bp or the t of the net-of-30-bp spread, the smaller of the month- and quarter-clustered t, is below 1.0, on each of the four readings (raw, beta-adjusted, size-tight, size-tight and beta-adjusted; any failing reading kills); short (late minus matched peers, 30 bp per side, 30 bp a year borrow) is killed if its mean net is below +60 bp or its t is below 1.0 on any of the four (any failing reading kills). A statistic that cannot be computed is a kill. The matched-peer leg is taken as costless (a pass is an upper bound).

**C1b's verdict is "both killed on survivor data".** C1c reads it as follows, and only this way:

- **C1b's verdict stands** for a thesis if C1c's kill line fires on A for that thesis.
- **C1b's verdict changes** for a thesis if the kill line does not fire on A **and fires on B**. C1 was retired by the owner (TP-8), so this is a pass of a kill-only rule that the owner may take to rung 3 of the [ADR 0023](../decisions/0023-break-first-fast-books-paper-exam.md) ladder (capacity and fills at $100) by un-retiring it; it is his call and not automatic, and it is not a strategy.
- **Control reading B**, computed in the same run: the same pipeline without the events whose permaticker is delisted within the window (TICKERS `isdelisted = Y` and last price date on or before 2023-12-29), so peers, buckets and spreads are recomputed without them. B holds the vendor's prices fixed and removes the delisted names. If A passes and B kills, the change is attributable to the delisted names, with the idealisation above (held at last close) stated beside it as the reason a pass is not robust. If A differs from C1b and B also differs from C1b, the change is not attributable to the delisted names; any attribution to the vendor's prices or coverage stays in the owner's private notes (ADR amendment point 5), and the public report says only "not attributable to delisted names". B has no kill authority; it is reported with its kill flags and its four-reading means.
- **A short kill on A whose delisting-return stress passes** (the stress below, applied to the late short's raw reading) is recorded as "stands, not robust to the delisting return".
- **Reported only:** the spreads of the delisted events alone (their peers are A's), 20-session spreads, the 15 bp lines, borrow lines 0 and 300 bp a year, raw and SPY-adjusted means, per-group counts, the exit types above. The delisted-only reading is a decomposition of A and not a thesis test.
- **Look-ahead, stated.** "Delisted" is a flag read from today's TICKERS table, used only to define B and the reported decomposition. It never selects an event in A, and every price, action and share count used for A is dated at or before close(2023-12-29). TICKERS' `isdelisted` for a name delisted after 2023-12-29 reads as a survivor at the boundary. SEP is the vendor's latest revision (it can carry corrections made after the date); it has no `known_at` and that is a limit, shared by every event.

## Event counts before any return (read-only)

Read on 2026-10-27 with the script's `counts` subcommand: dates and flags only, no price, volume or return value, the store opened read-only. Events are the 10-Q and 10-K events accepted 2016-07-01 to 2023-09-29 that carry a causal tercile label.

| | Events |
|---|---|
| Labelled events in the traded window (all three groups) | 158,890 (early 43,944, normal 68,821, late 46,125) |
| With a store bar on the entry session (C1b's bar source) | 105,074 |

The counts that come from Sharadar (events with a crosswalk, with an SEP entry-session bar and the 10 prior-session bar rule, split into delisted-in-window and survivor names, with and without a shares fact, by group, and how many also have store bars) are **not in this public file**: they describe the vendor's coverage, which is a licence section 8 matter (ADR amendment, point 5). They are in the owner's private file `~/tradepartner-probes/sharadar/c1c-private/c1c-counts.json` (SHA-256 `9c58fa4674fca040c96e1d40448c4dd3ee16ba4dd93109a7004a849187d9429d`), written before any return; the audit file `~/tradepartner-probes/sharadar/c1c-private/c1c-adjust-check.json` has SHA-256 `3468436ea86ddce657574b00a6f65de6c47eef8c59d57b13c55d828302fccc37`. The registry hashes this file, not those, so they are pinned here (they sit under the side-dataset directory and are in the MANIFEST). The run counts the price, liquidity, beta, peer and size-group drops on top, as C1b did.

## Side dataset (research-registry req 4, as amended 2026-10-27)

- **Dataset:** Sharadar TICKERS, SEP and ACTIONS bulk files of 2026-10-27 in `~/tradepartner-probes/sharadar/2026-10-27/raw/` (outside the store and git); MANIFEST at `~/tradepartner-probes/sharadar/MANIFEST.txt`; rule in the [ADR 0022 amendment](../decisions/0022-no-vendor-security-master.md#amendment-2026-10-27-2417-sharadar-prices-as-a-research-only-side-dataset-for-one-check).
- **Truncation:** every SEP and ACTIONS row dated after close(2023-12-29) is filtered out before use. TICKERS is a current-state snapshot (category, CIK, ticker, `isdelisted`) that cannot be cut by date; it is used only for the crosswalk and for B and the decomposition, and that is a stated look-ahead in the mapping (a CIK-to-security link as of today). The script's fixture (`uv run python ~/tradepartner-probes/c1lag/c1c_filing_delay.py --fixture`, run from a repo checkout) shows the result is identical with rows after the window end present or absent (including a split dated after it).
- **Recorded:** the run's exploratory field holds the SHA-256 of `tickers.parquet`, `stocks.parquet` and `actions.parquet`, and of the two scripts. The scripts as reviewed have SHA-256 `5551472bb80c8eb6b1dda3b1c8bb58b9934d89cbcaa61c6ed96600d75d88bf94` (`c1c_filing_delay.py`) and `e73a0598eb9692781e4bd1fe46926b8369dab4bb72d5f7c49fa720d31342e1d5` (`c1b_filing_delay.py`); a run on different scripts is not this registration's run unless the owner re-pins in a new PR.
- **Deadline:** the run must open before the subscription ends (the owner's cancellation date). A missed deadline is closed with `experiment abandon`; the 30-day deletion window is for deleting, not for running.
- **Key:** the script sets `TRADEPARTNER_ENV_FILE=/dev/null` before loading settings, so it never reads `.env`, and it never touches the Sharadar API key; it reads the already-downloaded files only.
- **Deletion:** within 30 days of the subscription ending (ADR amendment, point 3). The committed result is aggregate numbers only: kill flags, spreads, net means, t statistics and gaps (ADR amendment point 4); the coverage counts stay private; the per-event artifact stays under `~/tradepartner-probes/sharadar/c1c-out/`.

## Prior-evidence disclosure and N

- **C1 run 2 and C1b run 3 have been seen.** C1b's result, "killed on survivor data" (TP-8), motivated this file. This is a third look at the same idea and is disclosed as one: same events, same labels, same kill lines. It changes the price source, nothing else, so it is a data-coverage check of C1b and not a new hypothesis. C1b's 2016 to 2023 events are the same sample.
- **The family's N now includes both.** `filing_delay`'s research-run count is C1 run 2's 2 configurations plus C1b's 2; this run adds 2, to **6** (`n_configurations = 2`, the same two theses). The control reading B is a second computation of those same two configurations, not a third, and the delisted-only reading is a decomposition of A. Family N is not reset by a better data source.
- **Literature** as C1 and C1b: Bartov and Konchitchki (2017), Johnson and So (2018), Dai et al. (2021); the McLean and Pontiff haircut (QI-2) and LS-6 for the short leg.
- **Survivorship, after this run.** The vendor covers delisted names but its last bar is not a delisting return (above), its crosswalk is by current CIK, and it carries no point-in-time stamps. A pass is therefore an upper bound and a kill is a kill on the best delisted-inclusive data the owner has; neither is robust to the missing delisting return.
- **Not controlled:** filer status, within-bucket style and momentum, the delisting return, the CIK-change gap, SEP revision history.

## The dataset

`c1b-filing-delay-matched-events`: the same export as C1b (189,282 rows, from 2015-04-01 to 2023-09-29). The script's `run` refuses to start unless the bound file's accessions equal the events it rebuilds from the store. Split `full`, nothing sealed.

## Parameters

```toml experiment
slug = "c1c-filing-delay-delisted"
kind = "return"
stage = 2
title = "C1c filing delay on Sharadar bars: does adding delisted filers change C1b's verdict?"
confirmatory = false
provenance = "deterministic"
touches_returns = true
family = "filing_delay"
claims = ["QI-2", "LS-6", "TP-8"]
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
inference = "C1b's design with bars from the Sharadar SEP and ACTIONS side dataset (delisted filers included): per-event 60-session spread (the early-tercile event's return from the next session open after acceptance to the close of the 60th session, adjusted for splits and cash and spin-off dividends rebuilt from raw closes and ACTIONS, minus the equal-weight mean return over each peer's own entry-to-exit span of the same-form, same-size-tercile normal-lag events entered within 5 sessions), minus 60 bp (30 bp per side round trip), in bp; terciles are causal; a name with no bar after its last bar is held at its last close; mean over events, standard error clustered by acceptance month, each bound one-sided 95% (mean -/+ 1.645 SE). No threshold, so the registry verdict is n/a. Kill rules are C1b's, word for word, applied to reading A (all crosswalked events): every t is the smaller of the month- and quarter-clustered t; a statistic that cannot be computed is a kill; each rule must hold on four readings or it fires. C1b's verdict stands for a thesis if its kill line fires on A and changes if it does not. SPY is a beta regressor only and is in no benchmark"
secondary = ["early_20_n", "early_20_net15_bp", "early_20_net30_bp", "early_20_raw_bp", "early_20_spread_gross_bp", "early_20_spy_adj_bp", "early_20_t_min", "early_20_t_month", "early_20_t_quarter", "early_60_badj_gross_bp", "early_60_badj_t_min", "early_60_beta_gap", "early_60_logmcap_gap", "early_60_n", "early_60_net15_bp", "early_60_net30_bp", "early_60_raw_bp", "early_60_spread_gross_bp", "early_60_spy_adj_bp", "early_60_sz_gross_bp", "early_60_sz_t_min", "early_60_szb_gross_bp", "early_60_szb_t_min", "early_60_t_min", "early_60_t_month", "early_60_t_quarter", "kill_long", "kill_short", "late_20_n", "late_20_raw_bp", "late_20_spread_gross_bp", "late_20_spy_adj_bp", "late_60_badj_gross_bp", "late_60_beta_gap", "late_60_logmcap_gap", "late_60_n", "late_60_raw_bp", "late_60_spread_gross_bp", "late_60_spy_adj_bp", "late_60_sz_gross_bp", "late_60_szb_gross_bp", "n_bad_div", "n_delisted_exits", "n_dropped_horizon_early", "n_dropped_horizon_late", "n_dropped_horizon_normal", "n_dropped_liquidity_early", "n_dropped_liquidity_late", "n_dropped_liquidity_normal", "n_dropped_no_beta_early", "n_dropped_no_beta_late", "n_dropped_no_beta_normal", "n_dropped_no_entry_bar_early", "n_dropped_no_entry_bar_late", "n_dropped_no_entry_bar_normal", "n_dropped_no_mcap_early", "n_dropped_no_mcap_late", "n_dropped_no_mcap_normal", "n_dropped_no_peers_early", "n_dropped_no_peers_late", "n_dropped_no_peers_normal", "n_dropped_price_floor_early", "n_dropped_price_floor_late", "n_dropped_price_floor_normal", "n_dropped_small_size_group_early", "n_dropped_small_size_group_late", "n_dropped_small_size_group_normal", "n_events_early", "n_events_late", "n_events_normal", "n_events_total", "n_no_label", "n_outside_chosen_span", "n_securities", "n_traded_early", "n_traded_late", "n_traded_normal", "n_unmapped_early", "n_unmapped_late", "n_unmapped_normal", "short_late_20_c15_b0_net_bp", "short_late_20_c15_b300_net_bp", "short_late_20_c15_b30_net_bp", "short_late_20_c30_b0_net_bp", "short_late_20_c30_b300_net_bp", "short_late_20_c30_b30_net_bp", "short_late_20_t_min", "short_late_20_t_month", "short_late_20_t_quarter", "short_late_60_badj_c30_b30_net_bp", "short_late_60_badj_t_min", "short_late_60_c15_b0_net_bp", "short_late_60_c15_b300_net_bp", "short_late_60_c15_b30_net_bp", "short_late_60_c30_b0_net_bp", "short_late_60_c30_b300_net_bp", "short_late_60_c30_b30_net_bp", "short_late_60_sz_c30_b30_net_bp", "short_late_60_sz_t_min", "short_late_60_szb_c30_b30_net_bp", "short_late_60_szb_t_min", "short_late_60_t_min", "short_late_60_t_month", "short_late_60_t_quarter"]
comparison_set = "two configurations on one window and one event list: the early-filer long and the late-filer short, each as a spread against size-matched, time-aligned normal-lag peers, at 20 and 60 sessions and 15 and 30 bp per side, borrow lines 0, 30 and 300 bp/yr for the short; reading B (the same pipeline without delisted names), the delisted-only decomposition and raw and SPY-adjusted means are reported for the record only"

[multiplicity]
method = "none"

[budget]
runs = 1
configurations = 2
stop_rule = "one run, one pre-declared design: no other horizon, lag definition, cutoff rule, size bucket count, peer window, universe filter, cost line, price source, delisting-return rule or window; no selection among the reported lines. A failed or refused run is reported, not re-run in place; a second look is a new registration that discloses this one, C1b and C1 run 2. Kill-only: each thesis is killed by its own pre-declared line; a pass sends it to rung 3, not to trading"
expected_effect = "spreads against matched peers near zero to a few tens of bp per event at 60 sessions, as in C1b; the added events should tighten the clustered standard error relative to C1b's; the most likely result is that both lines still kill, which would let C1b's verdict stand on the best delisted-inclusive data available"
```

## Expected magnitudes and red flags

- **A spread above 300 bp gross:** suspect look-ahead (an entry on the acceptance session, a label that used a later filing, a row after close(2023-12-29)) or a missed corporate action. Check the artifact's events against the vendor's files and rebuild three events by hand.
- **`n_unmapped_*` above a third of a group's events:** the crosswalk is thin (the current-CIK gap); read before trusting the result.
- **The audit below its 90% bar:** the adjustment rebuild is wrong; fix the script and re-pin before registering.
- **A and B giving opposite verdicts:** the delisted names carry the result, which is the finding; read `n_delisted_exits` and the exit types before concluding anything about delisting returns.
- **`early_60_raw_bp` and `late_60_raw_bp` far from C1b's:** the vendor's bars differ from Alpaca's; compare the raw means before the spreads.

## Retirement condition

One run retires this design whatever it shows. The kill rules are applied once, in the report, together with the verdict reading above; the owner decides what a kill or a changed verdict does. No variant (another horizon, bucket count, peer window, a delisting-return stress, another price source or an NT-form signal) is run under this slug; a later registration discloses this one, C1b, C1 run 2 and the family's N. The Sharadar side dataset is deleted within 30 days of the subscription ending, so this design cannot be re-run after that date without a new purchase and a new ADR.
