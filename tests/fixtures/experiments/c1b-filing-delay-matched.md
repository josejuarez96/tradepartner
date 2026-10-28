# C1b frozen parse-only study fixture; never register this slug.

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
[study]
configurations = 2
horizons = [20, 60]
groups = ["early", "late", "normal"]
peer_group = "normal"
entry = "next_session_after_date"
facts_read = "window_end"
bucket_by = ["quarter", "stratum"]
peer_by = ["stratum"]
n_buckets = 3
min_bucket_events = 30
price_floor = 5.0
volume_floor = 1000000.0
volume_lookback = 20
min_volume_bars = 10
shares_stale_days = 400
shares_fact = "shares_outstanding"
benchmark_security = "SPY"
peer_window = 5
min_peers = 5
near_k = 10
beta_lookback = 120
beta_min_bars = 60
cost_bps = [15.0, 30.0]
borrow_bps = [0.0, 30.0, 300.0]
z = 1.645
cluster_by = ["month", "quarter"]

[[study.kill]]
name = "long"
side = "long"
group = "early"
horizon = 60
readings = ["raw", "beta_adjusted", "size_tight", "size_tight_beta_adjusted"]
metric = "gross_spread_bp"
floor = 60.0
t_metric = "net_bp"
t_floor = 1.0
t = "min"
cost_bps = 30.0
borrow_bps = 0.0

[[study.kill]]
name = "short"
side = "short"
group = "late"
horizon = 60
readings = ["raw", "beta_adjusted", "size_tight", "size_tight_beta_adjusted"]
metric = "net_bp"
floor = 60.0
t_metric = "net_bp"
t_floor = 1.0
t = "min"
cost_bps = 30.0
borrow_bps = 30.0
```
