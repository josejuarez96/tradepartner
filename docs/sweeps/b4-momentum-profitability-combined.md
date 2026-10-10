# Sweep: B4, momentum + profitability combined by equal ranks (one variant)

<!--
Merging this file does not register it. B4 is parked (#1301 decision 4); it stays
unregistered until the owner unparks it. When he does, and only after the store carries
the development boundary row of ADR 0016 (`tradepartner decision development-boundary
--date 2023-12-29 ...`), he runs `tradepartner sweep register
docs/sweeps/b4-momentum-profitability-combined.md`, then `tradepartner sweep run
b4-momentum-profitability-combined`. The file is frozen once registered.
-->

**Family:** combined (child of `momentum`)  ·  **Author:** team hypfiles (agent draft); window, holdout and kill rule by Jose Juarez on #1292 (2026-10-08, option A) and ADR 0016 point 5  ·  **Date:** 2026-10-10  ·  **Issue:** #1292

## Why this is a sweep file, not a hypothesis file

Issue #1292 asks for `docs/hypotheses/b4-...`. The lab no longer accepts that: after the
strategy lab was initialised, a standalone hypothesis file registers only as a promoted
file (`sweep promote`), so a new family enters as a one-value sweep (team b4file's dry run
on #1292, 2026-10-08, exit 2 on `hypothesis register`). The owner decided the same on
#1292 ("Enter as a one-variant sweep file (the lab requires it)"). `combined` has no
sweepable keys (`config.FAMILIES["combined"].sweepable_keys == ()`), so the one grid axis
is the cadence, at the one value `month_end`: **one variant**, the B4 hypothesis itself.

## The hypothesis

**Claim.** A long-only book of the top 10% of the top-1000 universe, ranked on the equal
average of each name's 12-1 momentum rank and its gross-profitability (GP/A) rank,
rebalanced monthly to equal weight, does better net of costs than the better of the two
single signals over the same months.

**Economic rationale.** Momentum and profitability are close to uncorrelated as signals
and partly offsetting in their crash months, so a combined rank keeps names that score
well on both and drops the ones whose rank is driven by one noisy input. The backlog's
B4 prior is "a modest gain over either alone, mostly from lower turnover and
diversification" ([hypothesis-backlog.md](../research/hypothesis-backlog.md), B4). The
evidence is graded **G4-2 MIXED** for long-only, liquid portfolios and **G4-1 SUPPORTED**
only for long-short or unconstrained ones ([claims.toml](../research/claims.toml)); this
is a long-only book, so the MIXED grade is the one that applies. The weights are fixed in
advance (equal ranks, T130's `combined_rank`), never fitted on returns (HO-10).

## Parameters

Every value is decided before any B4 run, each with its source:

- `in_sample_start = 2020-08-31`: H1's and B3's, so B4 practises on the same months
  (owner, #1292 option A; ADR 0016 point 5).
- `[holdout]` **2026-11-01 to 2027-04-30**: a **forward** holdout (ADR 0016 point 4). It
  must start after `momentum`'s `holdout.end` (2026-09-30, the child rule) and after the
  registration day, and must not overlap any non-oracle family's holdout (`profitability`
  holds the same months as `momentum`). The owner's word on #1292 was "forward data from
  2026-10-01"; October 2026 has already begun, so it can no longer be forward at
  registration, and the first whole month after this file is 2026-11. Six month-ends
  (2026-11-30 to 2027-04-30) is `paper.min_rebalances` at `month_end` (ADR 0017's
  per-cadence default 6). **If the owner registers on or after 2026-11-01, move both
  dates forward by whole months before registering**; the exam is the paper book, and a
  holdout whose start has passed at registration is not forward.
- `[strategy]`: H1's values ([h1-momentum-12-1](../hypotheses/h1-momentum-12-1.md)).
  `top_fraction` and `weighting` there are momentum's own portfolio rules and are not read
  by the combined book; they are frozen because `combined` freezes and hashes the whole
  `strategy` section (ADR 0014 points 2 and 6). `turnover_top_fraction` is left at its
  default 1.0 (no screen); a non-default value is refused for this family.
- `[profitability]`: B3's values ([b3-gross-profitability](../hypotheses/b3-gross-profitability.md)),
  for the same reason.
- `[combined]`: `top_fraction = 0.10`, `weighting = "equal"`: H1's and B3's book size and
  weighting, so the comparison is like for like (`CombinedConfig` defaults).
- `[costs]`, `[universe] top_n_by_cap`, `[execution]`, `[alpaca]`: H1's and B3's.
- `[universe]` exception lists and `[master] keep_successors`: pinned to the `momentum`
  family rules' values, as the first sweep pins them
  ([momentum-topfrac-cadence](momentum-topfrac-cadence.md), "Parameters"), so a later
  `.env` entry cannot move B4's frozen set and B4 reads the same universe as H1.
- `[lab]`: below, under "Expected magnitudes" and "Retirement condition".

```toml sweep
slug = "b4-momentum-profitability-combined"
family = "combined"
parent_family = "momentum"
title = "B4: momentum + gross profitability, equal ranks, top 10% equal weight, monthly"
in_sample_start = 2020-08-31

[holdout]
start = 2026-11-01
end = 2027-04-30

[combined]
top_fraction = 0.10
weighting = "equal"

[strategy]
formation_months = 12
skip_months = 1
top_fraction = 0.10
weighting = "equal"
signal_total_return = true

[profitability]
basis = "gross"
annual_period_days = [350, 380]
max_fact_age_days = 548
exclude_sic_ranges = [[6000, 6999]]
include_derived = true
top_fraction = 0.10
weighting = "equal"

[costs]
per_side_bps = 15.0
commission_per_share = 0.0
commission_per_order = 0.0
sensitivity_per_side_bps = [0.0, 30.0, 60.0, 100.0]

[schedule]
# rebalance_cadence is the one-value grid axis below
signal_anchor = "month_end"

[universe]
top_n_by_cap = 1000
accepted_price_jumps = []
accepted_same_day_pairs = ["0001903392@2024-03-14"]
accepted_shares_facts = []

[master]
keep_successors = ["0000891103@2020-08-10"]

[execution]
fill_price = "close"

[alpaca]
historical_feed = "sip"

[grid]
"schedule.rebalance_cadence" = ["month_end"]

[lab]
selection_statistic = "excess_cagr_spy"
expected_excess_cagr_spy_pp = 0.25
expected_range_pp = [-2.0, 2.0]
promote_at_least = 0.5
retire_below = -0.0328
```

## Expected magnitudes and red flags

**Net excess return over SPY (base cost level, 15 bp per side).** Prior centred on
**+0.25 pp/yr**, plausible range **−2 to +2 pp/yr**: between H1's prior (0, range −1 to
+1) and B3's (0 to +0.5, range −2 to +2), as the backlog's "modest gain over either
alone" asks, and no wider than B3's because a combined rank sits between its parts.
Single-year gaps against SPY of ±10 to 15 pp are normal for a book of about 60 to 100
names. **Turnover** should be below H1's in-sample 32.4% a month, since a name leaves the
book only when its average rank falls, which is the backlog's mechanism; a combined book
that trades more than H1 contradicts the prior.

**Red flags** (a prompt for an audit, never a gate):
- base-level `excess_cagr_spy` above `metrics.red_flag_excess_cagr_pp` (3.0 pp): spec
  req 15 marks it `red_flag`;
- B4 beating **both** H1 trial 2 and B3 trial 3 by more than the whole prior range
  (4 pp/yr): an average of two ranks should not beat both parts by that much in 40 months;
- `n_combined` near zero or near `n_ranked` in every month: the combination is either
  empty or degenerate (one sub-signal is missing for most names: `one_signal_only`);
- any trial whose window reads a session after 2023-12-29 in sample: the development
  boundary was not in force (see "Precondition" below).

## Power arithmetic

40 monthly returns in sample, as H1's and B3's (the same window and calendar). N for the
`combined` family starts at 1 with this one variant, but its SR\* high-water mark is
seeded from `momentum`'s at registration (strategy-lab spec, child families; ADR 0014), so
B4 clears no lower bar than momentum's two counted trials and the first sweep's six. At
H1's 8.4% tracking error, each pp/yr of excess is worth about t ≈ 0.30 over the window
(B3's arithmetic). The kill line below is about −3.3 pp/yr, so a B4 result inside the
prior clears it by about one standard error; the run cannot show B4 is better than either
part with confidence, only whether it is clearly worse. That is why its exam is the
forward paper book, not this in-sample run.

## Prior-evidence disclosure

Seen before this file was written:
1. **H1's in-sample trial 2** on the owner's store, 2020-08-31 to 2023-12-29: base-level
   `excess_cagr_spy` −5.77 pp/yr (#503; the first sweep's disclosure).
2. **B3's in-sample trial 3**, same window: base-level `excess_cagr_spy` −3.28 pp/yr
   (team b4file's comment on #1292).
3. **H1's holdout trial 4** (2024-01-01 to 2026-09-30): +4.44 pp/yr over SPY. B4 never
   reads those months: its practice ends at the boundary and its exam is forward.
4. Everything H1's and B3's files disclose.

No B4 result has been seen on any store: team b4file's dry runs on #1292 ran
registrations only, no backtest. The two in-sample results above set the kill line, which
is why that line is fixed here, before any B4 run. Both sub-signals were seen to lose to
SPY in this window, so a combination of two losers is a priori unlikely to win; that is
recorded, not used to change any value.

## Precondition: the development boundary

Before `sweep run`, the store must carry the owner's `development_boundary` row at
2023-12-29 (ADR 0016 point 1). With it, B4's default in-sample window is [2020-08-31,
2023-12-29], the same as H1's and B3's, and 2024-01 to 2026-10 are B4's dead months. Without
it, the default window runs to the last rebalance before 2026-11-01 and reads `momentum`'s
spent exam and `profitability`'s unspent one. The owner must not run B4 until the row
exists.

## Retirement condition

Stated before any run and read the way H1's and B3's are. The reading is the first `ok`,
non-synthetic, in-sample trial of B4 over the full default window [2020-08-31,
2023-12-29] that passed `quant-auditor`, using its stored base-level `excess_cagr_spy`.

**B4 retires** (`tradepartner sweep retire b4-momentum-profitability-combined --reason
...`, nothing promoted) when that trial's base-level `excess_cagr_spy` is **below
−3.28 pp/yr** (`retire_below = -0.0328`, in the statistic's own units, a fraction): it
then does not improve, net, on the better single signal, B3 trial 3 (owner, #1292 option
A; the backlog's kill). "After counting the combination's trials" is the SR\* mark: a
promotion also needs `dsr_excess` at or above 0.5 (`promote_at_least`, the spec's
`lab.promotion_min_dsr_excess` floor) against the `momentum`-seeded mark. A B4 that clears
the kill line but not the DSR floor is neither retired nor promoted: it waits for its
forward exam, which is its own paper book (ADR 0016 point 4), opened only by the owner
(ADR 0017: B4 is not in the current book order).
