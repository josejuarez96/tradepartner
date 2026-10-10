# Hypothesis: the daily shakedown book, 12-1 momentum, top 20%, rebalanced every session (a machine test)

<!--
Merging this file does not register it. The owner runs
`tradepartner hypothesis register docs/hypotheses/momentum-daily-shakedown.md --operations-book-of momentum-topfrac-cadence--r1-v1 --reason "..."`
after merge, once the paper plan's T160b has landed (strategy-lab spec req 1, amendment
2026-10-10 (#1416)); the plain `hypothesis register` is refused on a lab-initialised store,
and so are `sweep promote` (req 4: the sweep's `retire_below` was met) and a one-value sweep
(req 1(b): this frozen set is sweep variant v1's). The file is frozen once registered: the
registry hashes the whole file, so any edit after registration is a new hypothesis.
-->

**Family:** momentum  ·  **Author:** team shakebook (agent draft); the decision by Jose Juarez on #1352 and #1416, 2026-10-10  ·  **Date:** 2026-10-10

**This is a machine-test book, not a strategy bet.** It exists so that one paper book trades
every session during the [ADR 0017](../decisions/0017-fast-paper-and-machine-readiness-gate.md)
shakedown (part E: at least M = 5 of N = 10 sessions with a scheduler-submitted order that
fills), so the machine's readiness lines can be shown in two weeks rather than two months.
Its parameters are sweep variant **v1** of
[momentum-topfrac-cadence](../sweeps/momentum-topfrac-cadence.md) (`daily`,
`top_fraction = 0.20`), chosen **for trading activity, not for performance**, after the
sweep's results were seen. The sweep lost to SPY on every variant: v1's base-level excess
CAGR over SPY was **−6.13 pp/yr** in sample (2020-08-31 to 2023-12-29), with a maximum
drawdown of about −33% and turnover of about 7.07 per year; the best variant (v5,
`month_end`, `top_fraction = 0.05`) was −2.58 pp/yr; the worst −6.42 pp/yr; `dsr_excess`
at most 0.33 against `promote_at_least = 0.5`; and the sweep's own retirement line
(`retire_below = −0.01`) was met, so `sweep promote` refuses every variant by design. **This
book is not expected to beat SPY.** It is an **operations book** under ADR 0017 part A
("paper as an operations test plus evidence"): it is never an exam of record, never a
forward holdout, never a counted selection under
[ADR 0016](../decisions/0016-development-boundary-and-forward-exams.md) point 3, and it
can never spend the family's holdout (strategy-lab spec req 5(b): it is neither pre-lab nor
promoted). Its tracking check (ADR 0005 criterion 1) is reported like any book's and judges
the machine against this strategy's own backtest, not the strategy against anything.

## Economic rationale

**The claim.** None beyond H1's. The signal is H1's
([h1-momentum-12-1](h1-momentum-12-1.md)): 12-1 momentum on the ADR 0006 top-1000
universe, equal weight, total-return signal, anchored at month ends. The two things that
differ from H1 are construction choices the sweep tested: a book twice as wide (the top
20%, about 200 names) and a rebalance to equal weight **every session** instead of monthly.
The sweep file's rationale for the axes stands; its prior for every variant was H1's,
excess over SPY centred on zero, and the sweep's answer was that both the wider book and
the faster rebalance trail SPY net of costs, as the file's "Expected magnitudes" said a
faster cadence would (more re-weighting trades at 15 bp per side; the ranking only changes
once a month at the `month_end` anchor).

**Why this variant, then.** The shakedown needs a book whose scheduled run submits orders
on most sessions. At `daily` cadence the ranking is monthly but the equal-weight targets
drift every session, so every session's run has sells and buys to place; at `month_end`
(H1's book `main`) a run trades on one session a month. Between the two `daily` variants,
v1 (top 20%) holds about twice the names of v2 (top 5%), so each session's re-weighting
is spread over more, smaller orders, which is more of what the order path, the
reconciliation and the journal must be shown to handle. That is the whole reason. The two
variants' returns and drawdowns (disclosed below) played no part in it, and neither
variant would have been chosen on them. The choice was made after the sweep's results
were read, and it is recorded as such here and in the `operations_book` decision row the
registration writes.

## Parameters

The block below is the only part the registry parses. It reproduces variant v1's frozen
set exactly: the sweep's fixed block with the two axis values v1 took, pinned where the
sweep file pinned (the owner-edited `universe.*` exception lists and
`master.keep_successors`, so a later `.env` entry cannot move the canonical set), and
nothing else named, so every other frozen key takes the live config value at registration
as it did at the sweep's. The registration is refused if the resulting canonical frozen set
differs from v1's in any key (the same check `sweep promote` makes), so the owner registers
with the live settings the sweep was registered under; a refusal names the key.
`strategy.turnover_top_fraction` is left out on purpose: it is a post-registration key
frozen at its default 1.0 (no screen), as it was for the sweep.

```toml hypothesis
slug = "momentum-daily-shakedown"
family = "momentum"
title = "Daily shakedown book: 12-1 momentum, top 20% equal weight, rebalanced every session (machine test; sweep variant v1)"
in_sample_start = 2020-08-31

[holdout]
start = 2024-01-01
end = 2026-09-30

[strategy]
formation_months = 12
skip_months = 1
top_fraction = 0.20
weighting = "equal"
signal_total_return = true

[costs]
per_side_bps = 15.0
commission_per_share = 0.0
commission_per_order = 0.0
sensitivity_per_side_bps = [0.0, 30.0, 60.0, 100.0]

[schedule]
rebalance_cadence = "daily"
signal_anchor = "month_end"

[universe]
accepted_price_jumps = []
accepted_same_day_pairs = ["0001903392@2024-03-14"]
accepted_shares_facts = []

[master]
keep_successors = ["0000891103@2020-08-10"]
```

Where each value comes from:
- `in_sample_start`, `[holdout]`, `[strategy]` except `top_fraction`, `[costs]`: the
  momentum family rules (H1's registration), copied by the sweep file's fixed block.
- `strategy.top_fraction = 0.20` and `schedule.rebalance_cadence = "daily"`: v1's two axis
  values from the sweep's `[grid]`.
- `schedule.signal_anchor = "month_end"`: the sweep's fixed value (the spec's default).
- `[universe]` and `[master]` pins: the sweep file's, which equal the family rules'
  `fixed_params_json`.

The holdout is the family's **historical, spent** one (H1's trial 4), so under ADR 0016
point 4 this is not a forward holdout (an operations file of a forward-holdout family is
refused, strategy-lab spec req 1 as amended), and `paper start` treats it as a completed one:
`holdout.end` 2026-09-30 is a completed `daily` rebalance session, and T_0 is the first
session strictly after the day the book starts. The window's `paper.min_rebalances` is the
`daily` entry of the per-cadence table (63, ADR 0017 open question 2).

## Expected magnitudes and red flags

The expectation is the sweep's result, already seen: **about −6 pp/yr** excess over SPY at
the base cost level, with the cost drag of a daily re-weighting on top of H1's. A paper
book over the shakedown's two weeks says nothing about any of that, and is not meant to.

What the book is for, and what counts as a red flag **for the machine**:
- The scheduled run should submit at least one order on most sessions. A string of
  sessions with no order at all (every target inside the risk rules' minimums, so the daily
  re-weighting trades nothing) means this book does not serve ADR 0017 E.1's M, and the
  owner chooses another activity source before the span opens; this is a machine finding,
  not a strategy one.
- The tracking check (ADR 0005 criterion 1, `paper report`) compares the book with its own
  backtest over the same sessions. A gap outside `paper.tracking_k` times the modelled cost
  per rebalance points at the fill convention, the cadence path or the data, exactly as it
  would for H1; it is read as a defect to find, never as a verdict on the strategy.
- Any `mismatch` reconciliation, any `LimitBreachError`, any incomplete chain: ADR 0017
  E.2, E.3 and E.5, each of which restarts or fails the span.

A positive excess over SPY in the paper book would be a surprise against the sweep and is
read the same way as any red flag (`metrics.red_flag_excess_cagr_pp`): a prompt for an
audit, not evidence of an edge.

## Power arithmetic

None. This hypothesis has no exam. Its holdout is the family's and is spent; the book is not
a forward holdout; the shakedown span is ten sessions; and ADR 0017 part A says a paper
book's tracking check judges the machine, not the strategy. The tracking check at `daily`
reads at least 63 rebalance periods (a quarter) before `paper report` prints its verdict
line, and the owner may close the book before that once the shakedown has passed (part E)
without losing anything the lab counts.

What the registration costs the lab: one `ok` in-sample trial of this registration (the
gap sign-off `paper start` needs references a trial of this hypothesis, so `backtest
momentum-daily-shakedown` runs once over the default window). It is the same computation
as v1's counted trial (one of trials 11 to 16; same canonical frozen set, same default
window) and should reproduce it to the row. It counts in the momentum family's N like any `ok` in-sample trial
(N 8 → 9 on the owner's store, SR* moves by the eighth-to-ninth step) and, over the
default window, adds no (canonical set, window) pair to V (strategy-lab spec req 3: the
pair key is the canonical set, so this file never doubles v1's pair; a run over another
window would be a new pair, as it would for any hypothesis).

## Prior-evidence disclosure

Everything seen before this file was written, in the order it was seen:

1. **The T114 sweep's six counted trials** (trials 11 to 16, run 4, 2026-10-10, on the
   owner's store, in sample 2020-08-31 to 2023-12-29): every variant below SPY, from
   −2.58 pp/yr (v5) to −6.42 pp/yr; v1 (this file) −6.13 pp/yr, maximum drawdown about
   −33%, turnover about 7.07 per year; v2 (`daily`, top 5%) −6.26 pp/yr, drawdown about
   −47%; `dsr_excess` at most 0.33. The full per-variant table is `sweep report
   momentum-topfrac-cadence` on the owner's store. **This file's variant was chosen after
   these numbers were read**, for activity, as the top of the file says.
2. **H1's in-sample trials** (trials 1 and 2; −5.95 and −5.77 pp/yr) and **H1's holdout
   trial** (trial 4, +4.44 pp/yr over 2024-01 to 2026-09 at 15 bp): the family's spent
   exam, disclosed in the sweep file and repeated here because this book's months follow
   it.
3. **H1's paper book** `main` (window 1, open since 2026-10-09, first rebalance
   2026-10-30): no rebalance had run when this file was written.
4. **Everything H1's file and the sweep file disclose** (G1's sources, MTUM, MSCI and AQR
   figures through 2026; B3's in-sample trial). None grades a top-20% book or a daily
   rebalance.

No result for v1 over any session after 2023-12-29 has been seen.

## Retirement condition

This hypothesis is never retired on performance, because it makes no performance claim.
Its book closes (`paper stop --book daily`) when the owner says so: at the earliest once
`paper shakedown` exits 0 with this book inside the span (ADR 0017 part E) and the Phase 4
retro has its output; at the latest if the book stops serving its purpose (a session
pattern with no orders, above) or any live gate of part F needs the account. Closing it is
an ordinary Phase 4 window close with its req 15 readiness read; nothing in the lab moves.
If the owner ever wants this construction as a strategy, that is a new sweep in the
momentum family that earns a promotion under req 4; this registration cannot be promoted
and cannot spend the holdout.

## Operations-book provenance

This is **not** a `## Sweep provenance` section: the file is not a promoted file, and the
template reserves that heading for one. The registration is an operations file (strategy-lab
spec req 1, amendment 2026-10-10, #1416).

- **Sweep:** `momentum-topfrac-cadence`, registration id 1 (the owner's store, registered
  2026-10-08); complete on 2026-10-10 (sweep run 4, trials 11 to 16, all `ok`); promotion
  refused by req 4 (`retire_below` met, `promote_at_least` not met); retired by the owner
  with `tradepartner sweep retire momentum-topfrac-cadence --reason ...`.
- **Variant:** `momentum-topfrac-cadence--r1-v1` (`daily`, `top_fraction = 0.20`), **not
  the argmax** (v5 was), chosen for trading activity.
- **Base-level in-sample statistics of the variant, as reported 2026-10-10:**
  `excess_cagr_spy` −6.13 pp/yr, `max_drawdown` about −33%, `turnover_annual` about 7.07,
  `dsr_excess` below 0.5. The `operations_book` decision row copies the variant's stored
  statistics from the registry at registration; those, not this prose, are the record.
- **What the registration is not:** no `promotion` row names it, so `holdout.decide`
  refuses any spend by it (req 5(b)); it is not counted against the sweep's or the family's
  promotion caps, and the promotion identity of v1 stays unpromoted; N and V are unchanged
  by the registration itself (its one in-sample trial counts as every trial does, above).
