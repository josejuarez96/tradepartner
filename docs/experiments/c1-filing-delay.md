# Experiment: C1 filing delay: early and late filers by lag change, entered at the next open

**Kind:** return  ·  **Stage:** 2  ·  **Author:** team c1lag (issue #2347), for the owner  ·  **Date:** 2026-10-27

Merging this file does not register it. After it merges, the owner registers the file, registers the dataset (the filings' event list) and opens one run with `--configurations 2`. The [research-registry spec](../specs/research-registry.md) requires that order: register, then run. The run counts in the family's N (its req 9) and is refused if it touches a protected window (its req 5). The owner runs the script against that run id. The script lives outside the repo (`~/tradepartner-probes/c1lag/c1_filing_delay.py`). The registry hashes the whole file, so settle the text before registering. **No return of any kind has been computed on the real store when this text was written** (the feasibility read below counts filings and bars, nothing else).

## Rationale

The research report on candidate C1 ("the filing that did not arrive", PR #2339) ranks it first by information value per unit of cost: it uses the one data edge the store owns (exact acceptance times of 10-Q and 10-K filings), needs no new data, and has a kill line that can be written down before any return is seen. A firm that files later than it did a year ago is, on the published evidence, signalling bad news that prices absorb slowly; a firm that files earlier is signalling the opposite. This run tests both ends once, in-sample, on 2017 to 2019 acceptances, and answers one question: does the timestamp edge have any standalone return value at our costs. A pass sends the idea to rung 4 of the kill ladder ([ADR 0023](../decisions/0023-break-first-fast-books-paper-exam.md)), a design. It is not a strategy.

**The signal (point in time).** Per filing `f` of form 10-Q or 10-K (exact form; amendments excluded):

- **Acceptance.** `accepted_at(f)` is the earliest `known_at` of the filing's `statement_facts` rows (the first-accepted vintage; the store stamps `known_at` with the EDGAR acceptance instant, tz-aware UTC). The acceptance date is that instant in America/New_York.
- **Period end.** `period_end(f)` is the latest `period_end` among the filing's non-comparative facts.
- **Lag.** `lag(f)` is the number of XNYS sessions `s` with `period_end(f) < s <= acceptance date`, from the trading calendar.
- **Prior filing.** The same CIK's filing of the same form whose period end is 350 to 380 days earlier (the one nearest 365 days; ties go to the lexically smaller accession number). Only candidates accepted before `f` are considered. No prior filing, no event.
- **Lag change** `d(f) = lag(f) - lag(prior)`, in sessions. Negative is earlier than last year ("early"), positive is later ("late"). This is the owner's "acceptance minus the same firm's same-form filing a year earlier" with the fiscal-calendar drift removed (it measures against each period's own end, so a 52/53-week year does not read as lateness).
- **Terciles.** Within each acceptance calendar quarter **and form** (so a 10-K season is not ranked against 10-Q seasons), the lower and upper tercile cutoffs of `d` are the 1/3 and 2/3 linear-interpolated quantiles. **Early:** `d` strictly below the lower cutoff. **Late:** `d` strictly above the upper cutoff. **Normal:** the rest, ties at a cutoff included. A (quarter, form) group of fewer than 30 events gets no terciles and is not traded.
- **Security.** One security per CIK: the lowest `security_id` the as-of master gives the CIK. Every security in the master with a bar at entry is eligible. No other universe filter applies. **Survivorship, stated plainly:** the bar store holds almost no history for issuers that delisted during 2017 to 2019 (the feasibility read below), so those events drop at entry (`n_dropped_no_entry_bar_*` counts them by tercile). The sample is survivors. The direction of the bias is **unknown**: the dropped issuers are failures (which flatter a long and hurt a short) and also merger targets (whose takeover premiums would have helped a long), and both are filers of either tercile. So neither a kill nor a pass is robust to it. **A kill is recorded as "killed on survivor data"**; it retires the survivor-sample reading only, and the owner decides, with the `n_dropped_no_entry_bar_*` counts in hand, whether it retires C1 or waits for delisted bars. A pass is no stronger than the survivors.

**Known limitation, stated up front.** The tercile cutoffs of a quarter use every filing of that quarter, including ones accepted after a given event. The lag change of the event itself is known at its acceptance; its tercile label is not, until the quarter is over. This is the owner's definition and it is the registered rule. A causal reading (cutoffs from the same quarter's group one year earlier) is computed beside it as a descriptive secondary (`early_60_prevcut_gross_bp`, `late_60_prevcut_gross_bp`). A rung-4 design must use a causal cutoff whatever the registered rule shows.

**The entry.** The **open of the first session whose date is later than the acceptance date** (never the acceptance session, even for a pre-market filing, which is conservative by up to a day of drift). The event is dropped if the security has no bar at that open or SPY has none.

**The outcome.** For horizon `h` of 20 and 60 sessions: the **market-adjusted return** is the security's return from the entry open to the close of the `h`-th session (the entry session counts as the first) minus SPY's return over the same sessions. Both are read on the dividend-adjusted frame (`adjusted_prices_as_of` with dividends), from the as-of API only, at `t = close(window.end)` and never later. A name whose last bar falls before the exit is held at its last close while SPY keeps running (the engine's delisting rule in value); `n_delisted_exits` counts every such event, delistings and halts alike. An event whose exit would fall after the window end is dropped and counted. Tradability filters, fixed here: the raw close of the session before entry is at least $5, and the median dollar volume (raw close x volume) of the 20 sessions before entry, over at least 10 bars, is at least $1,000,000. Dropped events are counted by reason. The filters read bars as known at close(2020-03-31), not as of each entry date (accepted: the corrections between the two are negligible for a price floor and a median volume).

**Costs.** Per side 15 and 30 bp, charged on the stock leg at entry and exit (a round trip is 30 and 60 bp). The SPY offset is taken as free; this flatters both legs by about one or two bp a round trip and does not move any line below.

**Primary (the early-filer long).** The mean 60-session market-adjusted return of the early tercile, per event, **net of 30 bp per side**, in bp (`primary.metric`). Its standard error clusters events by acceptance month (36 clusters, no small-sample correction); each bound is one-sided 95% (mean -/+ 1.645 SE). Sixty-session holds overlap about three acceptance months, which month clusters ignore, so **every t in a kill line is the smaller of the month-clustered and the quarter-clustered t** (12 clusters).

**Second configuration (the late-filer short).** Short the late tercile at the same next open, with the same exit rule: the position earns minus the market-adjusted return, pays the same per-side costs, and pays a borrow fee of `b` bp a year pro rata for `h / 252` of a year, on `b` in {0, 30, 300}. The borrow lines are cost lines of one configuration, not configurations. **This is a second configuration and counts in the family's N** (`budget.configurations = 2`; multiple testing: two ends of one signal, one run). The early-filer long stays the primary.

**Shorts are paper-only until the signed engine exists** ([ADR 0024](../decisions/0024-lift-long-only-and-top-1000.md)). Anomaly short legs often net about zero after borrow (LS-6), there are no fractional shorts at $100 (LS-16), and small late filers are often hard to borrow, so the 30 bp a year line is optimistic for exactly the names this leg holds and the 300 bp line is the more honest one. The short is measured here so the ledger records it, not because the owner can trade it.

**The absence entry (not run).** The report's stronger form shorts a firm at the first session it is past its own prior-year date with no filing accepted. That is not computable cleanly from held data: the expected period end and form must be inferred from the last filing, NT 10-K and NT 10-Q notices are not in the store (`issuer_forms` lacks them), and a firm that went dark or delisted cannot be told from one that is late. The index also holds only filings that carry stored facts. So acceptance-only entry is the registered design. The absence entry is a separate registration after NT forms are ingested (S).

**What it decides (pre-declared).**

- **Long kill (the primary).** The run kills the early-filer long if the early tercile's mean 60-session market-adjusted return is **below 60 bp per event** (the 30 bp per side round trip, so net of 30 bp per side below zero), **or** the t statistic of its net-of-30-bp mean against zero is **below 1.0** (the smaller of `early_60_t_net30` and `early_60_t_quarter_cluster`). 60-session is the primary horizon. 20-session is reported, not decisive.
- **Short kill (the second configuration).** The run kills the late-filer short if the mean 60-session net return of the short, **at 30 bp per side and 30 bp a year borrow, is below +60 bp per event**, **or** its t (the smaller of `short_late_60_t_c30_b30` and `short_late_60_t_quarter_c30_b30`) is **below 1.0**. The other cost and borrow lines are reported beside it. **The bar is deliberately asymmetric** (owner, 2026-10-27): the long is killed below 60 bp gross (net of costs below 0), the short below +60 bp net of costs and borrow, about 125 bp gross at these lines. A short has to clear costs and then 60 bp more because it is paper-only, its borrow is the least certain number here, and a short that merely breaks even is not worth an engine.
- **Reported for the record, not decisive:** the normal tercile, early minus late (`early_minus_late_60_gross_bp`, `early_minus_late_60_t`), the 20-session reads, the 15 bp lines, the quarter-clustered and iid t of the primary, the early tercile with exits on or before 2020-02-14 (the COVID crash falls inside the last quarter's 60-session windows), and the causal-cutoff reads.
- **Pass** means: the early long's kill did not fire. This run stands in for rungs 1 and 2 of the [ADR 0023](../decisions/0023-break-first-fast-books-paper-exam.md) ladder (cost and a stored-data read, at a flat 30 bp per side, the report's base line, not a measured slippage). A pass goes next to rung 3 (capacity and fractional fills at $100: roughly 15 events a day with overlapping holds, in small names) and then to a rung-4 design with a causal cutoff. It is **not** a strategy and not evidence for paper or live trading. A pass of the short alone means nothing until the signed engine exists. **Kill** means the early/late timestamp has no standalone return value at these costs on the survivor sample of this window; the ledger records it as killed on survivor data, the owner decides whether it retires C1 (see Security), and no variant is run under this slug.
- The registry's verdict is `n/a` (no `primary.threshold`): the kill rules read the point estimate and the t statistics, which one threshold cannot express.

**Outcome table** (the registered rule against its causal reading, `early_60_prevcut_*`, which covers events from 2018 on, with cutoffs from the same quarter and form a year earlier):

| Registered long | Causal read (`early_60_prevcut_net30_bp` above 0 and `_t_net30` at least 1.0) | Sends it to |
|---|---|---|
| killed | any | killed on survivor data; owner decides retirement |
| pass | holds | rung 3, then rung 4 with the causal cutoff |
| pass | fails or thin (small `early_60_prevcut_n`) | rung 4 only as a question about the cutoff rule; no rung 3 spend |

The short is read on its own line: a short kill is recorded as killed on survivor data and the owner decides whether it retires the late-filer short; a short pass waits for the signed engine and changes nothing about the long.

## Feasibility read (counts only, no returns)

Taken 2026-10-27 with a read-only DuckDB connection to the owner's store, before this file was written. It counts filings and security ids with bars. It reads no price, return or volume value. The "filing index" for 10-Q and 10-K acceptances is `statement_facts` (`form`, `filing_accession`, `known_at`); `filing_events` holds 8-K filings only. The year is the UTC year of acceptance.

| Year | 10-K accessions | 10-Q accessions | Same-form filing ~1 year earlier: 10-K | 10-Q |
|---|---|---|---|---|
| 2016 | 6,323 | 19,053 | (prior-year source only) | |
| 2017 | 6,024 | 18,333 | 5,547 | 16,873 |
| 2018 | 5,827 | 17,780 | 5,359 | 16,417 |
| 2019 | 5,704 | 17,270 | 5,203 | 15,985 |

- **Events with a computable lag change, 2017-01-01 to 2019-12-31:** 65,367 under the exact rules above (the dataset export; the table's UTC-year, period-end-gap read gives 65,384, within 0.03%).
- **With bars in the store** (the security has a bar on or before the acceptance date and one 90 or more days after it): 12,318 events in 2017, 13,159 in 2018 and 14,223 in 2019, from 3,181, 3,404 and 3,781 securities; 3,799 distinct securities across the window. About 60% of the events have a security with bars; the rest are issuers the bar store does not carry, or that left it within 90 days. Of the 3,799, 120 have their last bar before 2020-06-01. **The delisted issuers are largely not in the bar store:** the quant-auditor's count found 1,301 CIKs that filed in 2017 and stopped filing by mid-2019, of which 4 have any bars. This is the survivorship limit stated under Security above.
- No filing is missing a master `securities` row. Lag medians (days from period end to acceptance, 10-K 70, 10-Q 39) are sane.
- The exact post-filter event counts per tercile and horizon are an output of the run (`early_60_n` and the rest), not a pre-run number.

## Prior-evidence disclosure

- **Nothing from this signal has been seen.** No lag change, tercile or return of any filing has been computed on the store. The only reads are the counts above.
- **Literature (the C1 report, PR #2339).** Bartov and Konchitchki (*Accounting Horizons* 2017, Tier 1): first-time late filers fall about 2.9% (NT 10-Q) and 2.0% (NT 10-K) over five days around the notice and keep drifting down for a year (the drift size is unverified). Johnson and So (*JFQA* 2018, Tier 1): firms that schedule earnings later than expected report worse news that prices absorb slowly; the 260 bp monthly spread is a vendor figure (Wall Street Horizon, Tier 3, unverified in the paper). Dai et al. (*J. Corporate Finance* 2021): short interest rises before late filings. **Against:** RavenPack (vendor) says the date signal decays quickly, the delayer side faster; the McLean and Pontiff post-publication haircut of about 58% applies (QI-2); there is no net, post-2010, tradable test of either leg; and the late-filer universe skews small, where anomaly short legs net about zero after borrow (LS-6).
- **The report's own arithmetic.** At 30 bp a side the early long needs 60 bp a round trip; the published numbers put its edge near 55 bp per event, so rung 1 was undecidable from the literature. That is why this is a stored-data read.
- **Window.** Filings accepted 2017-01-01 to 2019-12-31, before every in-sample start (rule L4 in the report). The window's data bound is 2020-03-31 so the last events' 60-session exits are readable; nothing after that close is read. The momentum family's in-sample start and holdout (2024-01-01 to 2026-09-30) are untouched, and this registration's family has no protected window.
- **The COVID quarter.** Events accepted in late 2019 and early 2020 hold through the February and March 2020 crash. Returns are market-adjusted, so only beta differences between terciles matter; the ex-crash read is a secondary.
- **Filer status.** SEC deadlines depend on filer status (accelerated filers file sooner), and status follows public float, which follows past price. A firm growing into accelerated status files earlier by rule, so the early tercile may lean to past winners and the late tercile to past losers, which market adjustment does not remove. No diagnostic is registered for it; if the early tercile passes, rung 4 must control for it (for example by comparing with the tercile's 12-1 return before entry).
- **Family.** `filing_delay`, a root, experiment-only family (issue #2353, PR #2361), so this run's two configurations count in its own N and in no momentum, profitability or combined count. It has no hypothesis, backtest trial or protected window.

## The dataset

`c1-filing-delay-events`: one CSV with one row per event (65,367 rows): accession, security id, CIK, form, acceptance instant (UTC), acceptance date, period end, the prior filing's accession, acceptance and period end, both lags and the lag change. The script's `frame` subcommand writes it from the store (filings only, no prices). The event column is `acc_date`, 2017-01-03 to 2019-12-31, declared as 2017-01-01 to 2019-12-31. The split is `full`; nothing is sealed. It is the run's frame, not its market data: prices come from the store through the as-of API (req 4), and the script refuses to run unless the bound file's accessions equal the events it rebuilds from the store.

## Parameters

```toml experiment
slug = "c1-filing-delay"
kind = "return"
stage = 2
title = "C1 filing delay: early and late filers by lag change, entered at the next open"
confirmatory = false
provenance = "deterministic"
touches_returns = true
family = "filing_delay"
claims = ["QI-2", "LS-6"]
seed = 20261027
splits = ["full"]

[dataset]
name = "c1-filing-delay-events"

[window]
start = 2017-01-01
end = 2020-03-31

[primary]
metric = "early_60_net30_bp"
direction = "greater"
ci_level = 0.95
min_clusters = 30
inference = "per-event 60-session market-adjusted return (security minus SPY, entry at the next session open after the acceptance date, exit at the close of the 60th session, dividend-adjusted frame) of the early lag-change tercile, minus 60 bp (30 bp per side round trip), in bp; mean over events, standard error clustered by acceptance month (36 clusters), each bound one-sided 95% (mean -/+ 1.645 SE). No threshold, so the registry verdict is n/a. Kill rules (applied in the report; every t is the smaller of the month- and quarter-clustered t): long if the gross mean is below 60 bp or t (net mean over SE) is below 1.0; short if the late tercile short's mean 60-session net return at 30 bp per side and 30 bp/yr borrow is below +60 bp or its t is below 1.0"
secondary = ["early_20_gross_bp", "early_20_n", "early_20_net15_bp", "early_20_net30_bp", "early_20_t_net30", "early_60_gross_bp", "early_60_gross_ex_covid_bp", "early_60_n", "early_60_net15_bp", "early_60_net30_bp", "early_60_prevcut_gross_bp", "early_60_prevcut_n", "early_60_prevcut_net30_bp", "early_60_prevcut_t_net30", "early_60_t_iid", "early_60_t_net30", "early_60_t_quarter_cluster", "early_minus_late_20_gross_bp", "early_minus_late_60_gross_bp", "early_minus_late_60_t", "kill_long", "kill_short", "late_20_gross_bp", "late_20_n", "late_20_net15_bp", "late_20_net30_bp", "late_20_t_net30", "late_60_gross_bp", "late_60_n", "late_60_net15_bp", "late_60_net30_bp", "late_60_prevcut_gross_bp", "late_60_prevcut_n", "late_60_t_net30", "n_delisted_exits", "n_dropped_horizon", "n_dropped_liquidity", "n_dropped_no_entry_bar", "n_dropped_no_entry_bar_early", "n_dropped_no_entry_bar_late", "n_dropped_no_entry_bar_normal", "n_dropped_price_floor", "n_events_total", "n_securities", "n_with_tercile", "n_with_tercile_early", "n_with_tercile_late", "n_with_tercile_normal", "normal_20_gross_bp", "normal_20_n", "normal_20_net15_bp", "normal_20_net30_bp", "normal_20_t_net30", "normal_60_gross_bp", "normal_60_n", "normal_60_net15_bp", "normal_60_net30_bp", "normal_60_t_net30", "short_late_20_c15_b0_net_bp", "short_late_20_c15_b300_net_bp", "short_late_20_c15_b30_net_bp", "short_late_20_c30_b0_net_bp", "short_late_20_c30_b300_net_bp", "short_late_20_c30_b30_net_bp", "short_late_20_t_c30_b30", "short_late_60_c15_b0_net_bp", "short_late_60_c15_b300_net_bp", "short_late_60_c15_b30_net_bp", "short_late_60_c30_b0_net_bp", "short_late_60_c30_b300_net_bp", "short_late_60_c30_b30_net_bp", "short_late_60_t_c30_b30", "short_late_60_t_quarter_c30_b30"]
comparison_set = "two configurations on one window and one event list: the early-filer long (primary) and the late-filer short (market-adjusted against SPY, with borrow lines 0, 30 and 300 bp/yr as cost lines), each at 20 and 60 sessions and at 15 and 30 bp per side; the normal tercile and early-minus-late are reported for the record"

[multiplicity]
method = "none"

[budget]
runs = 1
configurations = 2
stop_rule = "one run, one pre-declared design: no other horizon, lag definition, tercile rule, universe filter, cost line or window; no selection among the reported lines. A failed or refused run is reported, not re-run in place; a second look is a new registration that discloses this one. Kill-only: the long and the short are each killed by their own pre-declared line; a pass of the long sends it to a rung-4 design, not to trading"
expected_effect = "the early tercile's 60-session market-adjusted return near zero to a few tens of bp per event gross (the report's published-number estimate is about 55 bp, below the 60 bp round trip), so the long more likely fails than passes; the late tercile's short a few tens of bp gross and near zero or negative net of borrow (LS-6); an early-minus-late spread well under 1 pp; about 20,000 to 30,000 traded events across all three terciles after filters; a clustered standard error near 15 to 40 bp, so only an effect above 40 to 60 bp can separate from zero"
```

## Expected magnitudes and red flags

- **An early or late 60-session mean above 3% (300 bp) gross:** suspect look-ahead (an entry on the acceptance session, or a filing's `known_at` that is not its acceptance) or a split missed by the adjusted frame. Check `max_read_t` against close(2020-03-31) and rebuild three events by hand.
- **`n_dropped_no_entry_bar` above half of the tercile events:** the bar store does not cover the filers the feasibility read counted; read before trusting the means.
- **`n_with_tercile` far from `n_events_total`:** small (quarter, form) groups were dropped; check the 30-event minimum.
- **`early_60_gross_ex_covid_bp` far from `early_60_gross_bp`:** the result rides on the crash windows.
- **The terciles' sizes far from equal:** ties at the cutoffs (lag change is an integer count of sessions) push events into `normal`; that is expected to some degree.
- **A positive early-minus-late spread but both legs negative:** a market-adjustment effect (SPY outperforming small caps in 2017 to 2019), not a timing edge.

## Retirement condition

One run retires this design whatever it shows. The kill rules above are applied once, in the report. A kill retires the survivor-sample reading of the C1 acceptance-timing design on this window and these rules (the owner decides whether it retires C1; see Security); it does not stop other filing-time ideas. No variant (another horizon, a causal cutoff, the absence entry, an NT-form signal) is run under this slug; a later registration discloses this one in its preamble.
