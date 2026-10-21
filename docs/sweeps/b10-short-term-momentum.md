# Sweep: B10, short-term momentum in the top turnover quintile, beside its no-screen control (two variants)

<!--
Merging this file does not register it. The owner runs `tradepartner sweep register
docs/sweeps/b10-short-term-momentum.md` after merge, then `tradepartner sweep run
b10-short-term-momentum`, in sample only (strategy-lab spec req 1 and 2; backtest plan
T165e). `strategy.turnover_top_fraction` reaches `lab.sweepable_keys` through the default
derived from `config.FAMILIES["momentum"]`: if `.env` sets `LAB__SWEEPABLE_KEYS`, it must
list the key or be unset, else `sweep register` refuses the grid. The file is frozen once
registered.
-->

**Family:** momentum  ·  **Author:** team b10sweep (agent draft); the two-variant grid and the control by Jose Juarez (B10-1 to B10-5 taken as recommended, #1358; "Yes draft the b10 sweep", 2026-10-20)  ·  **Date:** 2026-10-20  ·  **Issue:** #2106

This is the sweep file the hypothesis file
[b10-short-term-momentum](../hypotheses/b10-short-term-momentum.md) names: B10 enters the
lab as a sweep, because a standalone file registers only as a promoted one (strategy-lab
spec req 1 and req 5(b)). The fixed block below is that file's `toml hypothesis` block,
with `strategy.turnover_top_fraction` moved to the one grid axis. The grid has **two
values**: B10 itself (0.20) and the no-screen control (1.0), pre-declared variant (ii) of
the hypothesis file's trial budget. The owner took B10-5's recommendation: "run the control
(ii) in the same sweep as this file so the sign reading has both halves at once".

## Economic rationale for the axis and its values

**The question.** Among the top-1000 names, does last month's total return predict next
month's when the sort is confined to the fifth of names with the highest share turnover,
and does it do so differently from the same sort over the whole universe? Medhat &
Schmeling (RFS 2022; claim SH-9, INSUFFICIENT) find that one-month returns **continue**
among high-turnover stocks and **reverse** among low-turnover ones, with a largest-500
long-short spread of 0.42 %/month, gross, over 1963-2018. SH-1 (NOT SUPPORTED) says the
plain one-month sort has no edge in large caps, or reverses. The hypothesis file's
"Economic rationale" and "Design" give the mechanism, the turnover definition (rule 2) and
the screen (rule 3); nothing here changes them.

- **`strategy.turnover_top_fraction = 0.20`** (B10): rule 3's screen, the top fifth of the
  members with a usable turnover, about 200 names, then the top `top_fraction` (0.20) of
  them by one-month total return, about 40 names. It is the paper's high-turnover
  quintile.
- **`strategy.turnover_top_fraction = 1.0`** (the control, variant (ii)): no screen, the
  frozen default; the same one-month sort over the whole universe, then its top fifth,
  about 200 names. SH-1 says it should show no edge, or reversal. **Its sign beside B10's
  is the information this sweep buys** (hypothesis file, "Retirement condition", the sign
  reading). The control is not a bet; it is the other half of one comparison.

No other value is a counted trial. Variants (i) (`week_end` with the `offset` anchor) and
(iii) (`top_fraction = 0.10`) of the trial budget are not in this grid; each would be a
later sweep on the owner's word. `strategy.turnover_top_fraction` has no
`lab.axis_lattice` entry, so no continuous axis is implied.

1 × 2 = **2 variants** in **1 read group** (one cadence), far under
`lab.max_variants_per_sweep` (100).

## Parameters

Every value is the hypothesis file's `toml hypothesis` block, which is the `momentum`
family rules (H1's first registration) for every forbidden-prefix key. A frozen key this
block leaves out is read from the frozen-key defaults or live settings at registration and
refused there if it differs from the family rules (req 1(a)). As the first sweep pins them
([momentum-topfrac-cadence](momentum-topfrac-cadence.md), "Parameters"), the block pins the
owner-edited exception lists `universe.accepted_price_jumps`,
`universe.accepted_same_day_pairs`, `universe.accepted_shares_facts` and
`master.keep_successors` to the momentum family rules' values, so a later `.env` entry
cannot move this file's frozen set.

```toml sweep
slug = "b10-short-term-momentum"
family = "momentum"
title = "B10: long-only short-term momentum, turnover screen top 20% vs no screen, top 20% equal weight, monthly"
in_sample_start = 2020-08-31

[holdout]
start = 2024-01-01
end = 2026-09-30

[strategy]
formation_months = 1
skip_months = 0
top_fraction = 0.20
weighting = "equal"
signal_total_return = true
# turnover_top_fraction is the grid axis below

[costs]
per_side_bps = 15.0
commission_per_share = 0.0
commission_per_order = 0.0
sensitivity_per_side_bps = [0.0, 30.0, 60.0, 100.0]

[schedule]
rebalance_cadence = "month_end"
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
"strategy.turnover_top_fraction" = [0.20, 1.0]

[lab]
selection_statistic = "excess_cagr_spy"
expected_excess_cagr_spy_pp = -1.0
expected_range_pp = [-6.0, 3.0]
promote_at_least = 0.5
retire_below = -0.01
```

Where each value comes from:
- `in_sample_start`, `[holdout]`, `[strategy]` (except the axis), `[costs]`, `[schedule]`,
  `[universe] top_n_by_cap`, `[execution]`, `[alpaca]`: the hypothesis file's block,
  unchanged. `[holdout]` is the family's already-spent exam (H1's trial 4, #1301), never
  read by this sweep: `sweep run` spends no holdout (req 2, req 5(a)), and B10 asks for no
  repeat spend (hypothesis file, "Holdout and the paper book").
- `formation_months = 1`, `skip_months = 0` at `month_end` with the `month_end` anchor:
  the anchor is T itself, so refusal 1(d) does not apply (hypothesis file, "Cadence").
- `[universe]` exception lists and `[master]` pins: the momentum family rules' values, as
  the first sweep and the buy/hold sweep pin them.
- `[grid] "strategy.turnover_top_fraction" = [0.20, 1.0]`: B10 and the control (ii), the
  owner's B10-5. Both values validate (`gt=0, le=1`) and are distinct. At 1.0 the key is
  left out of the canonical set (`POST_REGISTRATION_OWN_KEYS`), so the control's
  fingerprint is the plain one-month sort's; no registration holds it.
- `[lab] expected_excess_cagr_spy_pp = -1.0` and `expected_range_pp = [-6.0, 3.0]`: the
  hypothesis file's "Expected magnitudes": the net prior over SPY is "centred at about
  −1 pp/yr, plausible range −6 to +3 pp/yr". One prior covers both variants: the
  control's prior (no edge or reversal, before the same cost drag) sits inside it.
- `[lab] selection_statistic = "excess_cagr_spy"` and `retire_below = -0.01`: the
  hypothesis file's retirement line, base-level net excess CAGR over SPY below
  **−1 pp/yr**, which is H1's, B3's and the two momentum sweeps' line.
  `excess_cagr_spy` is stored as a fraction (`metrics.py`): −1 pp/yr is written −0.01.
- `[lab] promote_at_least = 0.5`: the spec's `lab.promotion_min_dsr_excess` default, the
  lowest the parser accepts, and the `dsr_excess` half of the file's retirement rule.

Owner answers that set these values, one line each:
- B10-1 (a): `momentum` by amendment, one key (#1358; backtest spec amendment #1358).
- B10-2 (a): formation-month share volume over shares outstanding known at `t`,
  split-adjusted (built in T165b and T165c).
- B10-5: the file plus the three pre-declared variants; the control (ii) runs in this
  sweep beside B10.
- 2026-10-20: "Yes draft the b10 sweep".

## Expected magnitudes and red flags for the sweep

- **Excess return over SPY (base cost level, 15 bp per side).** B10's prior is the
  hypothesis file's: centred on **−1 pp/yr**, range **−6 to +3 pp/yr**. The gross long leg
  is about +2.4 pp/yr if it carries half of SH-9's spread (an unverified analogy), and
  costs at about 90 %/month one-sided turnover are 2 × 90 % × 15 bp ≈ 3.2 pp/yr, so the
  net is about zero at best. The control has no edge to offset the same cost drag, so its
  prior sits at or below B10's, inside the same range. Both variants inside the range is
  the expected outcome; over 40 monthly returns at about 10 %/yr tracking error, one
  outside it would not be surprising.
- **The sign reading** (pre-declared, a diagnostic, never a selection): same sign, the
  screen adds nothing; B10 above zero and the control below, SH-9's turnover mechanism is
  visible in our data; both negative, SH-1's null covers both.
- **Turnover and cost.** One-sided `turnover_monthly` about 85 to 95 % for B10; cost drag
  at 15 bp about 3 to 3.5 pp/yr, at the 100 bp rung about 20 to 23 pp/yr. The control's
  book (about 200 names) should turn over somewhat less. A `cost_drag` far from
  ≈ 12 × `turnover_monthly` × 2 × `per_side_bps` is a cost-model bug.
- **Red flags** (a prompt for a look-ahead and data audit, never a gate):
  - Either variant's base-level `excess_cagr_spy` above `metrics.red_flag_excess_cagr_pp`
    (3.0 pp): spec req 15 marks it `red_flag`.
  - B10's `turnover_monthly` far below 70 % or above 100 %: the screen or the sort is not
    doing what rules 3 to 5 say.
  - B10's `n_screened` far from a fifth of `n_universe` (about 200), or
    `n_excluded_no_turnover` above a few dozen at any rebalance: the shares read or the
    volume frame has a hole.
  - Any `n_screened`, `n_excluded_no_turnover` or `no_turnover` exclusion reported for
    the control: at 1.0 the screen is off, the counts are reported only below 1.0, and no
    name may be excluded for turnover (backtest spec amendment #1358).
  - A name's turnover moving by a split ratio (×2, ×4, ×10, ×20) between adjacent
    rebalances around an ex-date: rule 2's adjustment failed.
  - The two variants' results equal, or B10's holdings equal to the control's at most
    rebalances: the screen is not binding.
  - A survivorship gap above `gap.count_share_threshold` at any rebalance, a non-zero
    `n_static_listings`, or an `n_universe` below `universe.top_n_by_cap`.
  - Any variant with a `kind=holdout` trial. `sweep run` cannot spend the holdout.

## Power arithmetic per variant count

The `momentum` family's N before this sweep is **11** as the buy/hold sweep counted it
([momentum-hold-top-fraction](momentum-hold-top-fraction.md), "Power arithmetic"), **12**
once that sweep's one variant has run `ok`. After this sweep, with both variants `ok`,
**N = 13 or 14**. V is the family's, read at registration, floored at
`lab.min_sharpe_variance_annual` (0.04). At N = 14 the expected maximum Sharpe of a null
grid is about `√V · ((1 − γ)·Φ⁻¹(1 − 1/N) + γ·Φ⁻¹(1 − 1/(N·e)))` ≈ **1.74 · √V** (1.70 at
N = 13): about **0.35** annual at the V floor, more if the family's spread is wider.

At the assumed 10 %/yr tracking error over 40 monthly returns (3.3 years), each pp/yr of
excess is worth t ≈ 0.18, and a variant needs about **+3.5 pp/yr** of excess at the V floor
for its `dsr_excess` to reach 0.5. That is above the top of the prior range (+3) and above
the red-flag line (3.0 pp). **The sweep cannot separate an edge from selection, and
`promote_at_least` is reachable only by a result that is red-flagged first.** The
sweep's product is the sign reading, the turnover and cost slope, and the counts that check
the screen is point-in-time, as the hypothesis file says ("Power arithmetic").

## Prior-evidence disclosure

Results already seen before registration:
1. **SH-9's published figures** (Medhat & Schmeling, via the B10 research report): a
   largest-500 long-short STMOM spread of 0.42 %/month (t 4.91) gross, 1963-2018; no long
   leg reported, no US test after 2018 found, no Chen & Zimmermann series for the signal.
2. **H1's in-sample trials on the owner's store** (2020-08-31 to 2023-12-29, the window
   these variants run): trial 2 base-level `excess_cagr_spy` −5.77 pp/yr, trial 1
   −5.95 pp/yr. A 12-1 sort, not a one-month one; same family and window.
3. **H1's holdout trial** (trial 4, 2023-12-29 to 2026-09-30): +4.44 pp/yr over SPY, and
   the benchmark facts the hypothesis file lists for those months. Spent; never read here.
4. **The first sweep's six variants** (T114, trials 11-16, retired 2026-10-10): all lose to
   SPY, −2.6 to −6.4 %/yr. Same family and window, different signal.
5. **The daily shakedown's operations book** (T160, trial 17): a 12-1 book at
   `top_fraction = 0.20`, `daily`, −6.13 pp/yr in sample. Same family and window.
6. **The long/short spike** (T218, 2026-10-19): H1's ranking as a 50/50 book, net spread
   −6.82 pp/yr at 30 bp borrow, killed. Same family and window.
7. **The buy/hold sweep** ([momentum-hold-top-fraction](momentum-hold-top-fraction.md)):
   any result it has produced by registration day is a same-family, same-window result
   and is seen.
8. **Kill-ladder rung 1 for B10** (ADR 0023): the daily book's per-fill slippage
   (`paper report`, T203), read before this sweep runs; the sweep runs only if rung 1
   passes. It is a cost measurement, not a return.
9. **General knowledge**: the hypothesis file's paragraph applies unchanged.

**Not seen:** no TradePartner trial with a turnover screen, and no one-month momentum
trial of any kind, exists on any store. Every grid and `[lab]` value was fixed before any
run, from the hypothesis file.

## Retirement condition

The reading is the hypothesis file's, applied per variant: the first `ok`, non-synthetic,
in-sample trial of each variant over the full default window [2020-08-31, 2023-12-29]
that passed `quant-auditor`, read at its base level.

The sweep retires (`tradepartner sweep retire b10-short-term-momentum --reason ...`) and
promotes nothing when its argmax, the variant with the higher base-level
`excess_cagr_spy`, is below **−1 pp/yr** (`retire_below = -0.01`), the hypothesis file's
line. A variant promotes only through `sweep promote` and its rules (spec req 4): it must
be the argmax and reach `promote_at_least` against the family's mark, which the power
arithmetic above says no plausible result does. The sweep also promotes nothing while a
variant is `red_flag`ged, until the audit that flag opens has cleared it. The control is
never promoted to the hypothesis file: it is not B10.
