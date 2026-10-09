# Hypothesis: B10, long-only short-term momentum (STMOM), last month's winners among the highest-turnover names, monthly

**Family:** `momentum` (by amendment: one new frozen key, `strategy.turnover_top_fraction`; see "The family question")  ·  **Author:** team hypfiles (agent draft on Fable 5.1, #1358); owner approval to draft 2026-10-09 (#1353 shortlist, rank 2, accepted for its information value and as a fast paper book)  ·  **Date:** 2026-10-09  ·  **Status:** draft, not registrable: it needs a backtest spec amendment for the key and its engine tasks (listed under "The family question" below, which is the handoff; nothing is built), and the owner's answers to B10-1 to B10-5 below

Merging this file does not register it. The strategy lab's migration (T113) is merged,
so a new hypothesis enters as a **one-value sweep** (strategy-lab spec req 1 and req 5(b):
`hypothesis register` refuses a standalone file that is not a promoted one; "a new
hypothesis (an H2) is written as a one-value sweep, run, and promoted"). The owner
registers `docs/sweeps/b10-short-term-momentum.md`, whose fixed block reproduces the
parameter block below, in the `momentum` family, runs it, and promotes its one variant to
this file (which then gains `## Sweep provenance`). Before that, the key
`strategy.turnover_top_fraction` must exist (a reviewed config change behind a backtest
spec amendment, with a `FROZEN_KEY_DEFAULTS` entry of `1.0`, "no screen"; strategy-lab
spec, Out of scope: "a new `strategy.*` key by spec amendment ... with a
`FROZEN_KEY_DEFAULTS` entry"). **That entry alone does not keep H1's fingerprint still
under today's code:** backtest spec decision 13 (strategy-lab Definitions "Fingerprint",
amendment #951; `backtest/frozen.py` `canonical_frozen_set`) keeps every key of a
family's **own** section in the canonical set even at its default, so a new `strategy.*`
key would be overlaid at 1.0 onto H1's, the T114 variants', `oracle`'s and `combined`'s
stored params and move each recomputed fingerprint. The spec amendment must therefore
also amend decision 13's own-section rule, **narrowly**: a key added to a family's own
section **after that family's first registration** (its `FROZEN_KEY_DEFAULTS` schema
version later than the family's first-registration version, or an explicit
post-registration marker on the entry) is left out of the canonical set at its default
even in its own section, so a registration that never named it hashes as before, while
B10 (which names 0.20) and any later file that names 1.0 explicitly hash the same as one
that leaves it out (two registrations with one canonical set are one test, as the rule
intends). The carve-out must **not** reach keys that existed at a family's first
registration: every `profitability.*` key and both `combined.*` keys are post-baseline
entries in `FROZEN_KEY_DEFAULTS` too, and B3 registered at exactly their defaults, so a
rule keyed on the lab baseline rather than on the family's first registration would strip
B3's seven keys from its canonical set and move its fingerprint, the same hazard for
`profitability`. The alternative, re-recording every stored fingerprint under a named
data release, is the fallback the amendment may choose; either way the engine task's
pinned tests check the rule the amendment states, for H1, the T114 variants, B3 and
`combined` alike, not an assertion.
Under
research-program §4, promotion condition 1 (data) **is met**: bars with volume, the
shares-outstanding fact and corporate actions are in the store; condition 2 (engine) is
not met until the key and the screen land; this file is condition 3; its trial budget is
condition 4; condition 5 is the owner's. The registry hashes the whole file, so any edit
after registration makes a new hypothesis.

Backlog item [B10](../research/hypothesis-backlog.md#b10-short-term-momentum-stmom).
Claims it tests, by id in [claims.toml](../research/claims.toml): **SH-9** (INSUFFICIENT:
in-paper evidence to 2018, no post-2018 US test found) against **SH-1** (NOT SUPPORTED:
plain one-month reversal has no edge in large caps) and **SH-2** (costs). The research
rules are in [research-program.md](../ways-of-working/research-program.md).

## Economic rationale

**The claim.** A long-only portfolio of the top fifth, by last month's total return, of
the fifth of the ADR 0006 universe with the highest share turnover last month, equal-
weighted and rebalanced monthly, earns about the market's return net of costs. The prior
for its excess over SPY is **below zero at 15 bp per side** (the arithmetic is under
Expected magnitudes). That is not a claim of an edge. The owner accepted the test
(2026-10-09) for two reasons the report gives: it needs **no new data** (rank 2's "Data
we lack: none"), so it is the cheapest short-horizon test the system can run, and a
monthly book of about 40 names that turns over almost entirely each month gives the paper
machine many orders per rebalance (ADR 0017). Its information value is the sign: the
report warns that "the sign of a one-month sort flips with turnover", so this one file
tests SH-9 and SH-1's mechanism at once, and its pre-declared no-screen variant is the
control.

**Why the effect may exist.** Medhat & Schmeling (RFS 2022; SH-9) find that one-month
returns continue among high-turnover stocks and reverse among low-turnover ones; the
largest-500 long-short STMOM earns 0.42%/month (t 4.91) over 1963-2018, gross, the
megacap version 0.53%/month, and the full-sample strategy 1.00%/month net of half-spreads;
the effect "survives transaction costs and is strongest among the largest, most liquid",
and extends to 23 developed markets. The mechanism offered is that turnover separates
information-driven moves (which continue) from liquidity-driven ones (which reverse,
SH-1's inventory story in Nagel and in Dai et al.).

**Why it may be small or gone.**

- SH-9's sample ends in 2018 and the report found **no independent US test after
  publication**; the China evidence finds reversal only. INSUFFICIENT is the grade.
- The published numbers are long-short and gross; the long leg alone is not reported
  (rank 2's table). The report's long-leg proxy for the other candidates is a fraction of
  the spread; nothing says STMOM's is better.
- One-sided turnover is 89-90%/month in the paper, "a real chance of surviving costs"
  only at fills well below the backtester's 15 bp default (the report's Answer); Blitz
  et al. put the break-even of the short-term signals they study, at 1,300 to 2,000%
  annual turnover, below 25 bp.
- SH-1: in large caps the plain one-month sort is reversal at best and nothing at worst;
  a **mis-measured turnover** (unadjusted volume over a pre-split share count) sorts
  names into the wrong bucket and "gives a reversal book" (the report's STMOM note). Rule
  2 below exists for that.
- SH-2, QI-2, G2-S10: the post-2005 average anomaly is near zero net.

**Design.** H1's construction with two changes, the formation window and a turnover
screen, so that B10 is a `momentum`-family member (the family question) and a `momentum`
sweep can later vary the screen:

1. **Read time** `t = close(T)` for each rebalance session T, the last XNYS session of the
   month (`month_end`, ADR 0012); the universe is ADR 0006's at `t`.
2. **Turnover** for each member = the sum of SIP share volume over the sessions of the
   formation month (the sessions after the previous rebalance session through T) divided
   by shares outstanding known at `t`: the rule-7 fact (`EntityCommonStockSharesOutstanding`,
   `facts_as_of(t)`, `known_at` = its filing's acceptance, at most
   `universe.max_shares_age_days` old), with **both** series expressed in the share
   units of T: the share count is the universe's rule-7 pick (`shares_as_of`, whose
   `SharesPick.shares` is the **raw** filed count), moved by this signal as rule 8's cap
   loop moves it, by every split known at `t` whose ex-date falls **after the fact's own
   date** and at or before T (`as_of_date < ex_date ≤ T`; a fact dated after an ex-date
   is not adjusted again, and a fact dated before an earlier split is moved by that split
   too), and each session's volume is multiplied by the ratio of every split whose
   ex-date falls after that session and at or before T. A member with no usable shares
   fact at `t` (rule 7 already excludes it) or with a bar missing on **any** session of
   the formation month is excluded and counted (`n_excluded_no_turnover`), never given a
   turnover of zero.
3. **Screen:** the top `strategy.turnover_top_fraction` (0.20) **of the members with a
   usable turnover** (`n_universe − n_excluded_no_turnover`, the denominator) by
   turnover, ties by `security_id` ascending; about 200 names. Counted (`n_screened`).
4. **Score** inside the screened set: H1's momentum score with `formation_months = 1`,
   `skip_months = 0`, `signal_total_return = true`, `signal_anchor = month_end`: the
   total return from close(T minus one month-end) to close(T). Rank descending; ties by
   `security_id` (spec req 3).
5. **Portfolio:** the top `strategy.top_fraction` (0.20) of the screened set, about 40
   names, `weighting = equal`, long-only, remainder cash, one-month hold, through the
   existing `portfolio.target_weights`. No hysteresis band.
6. Everything else (fill convention, costs, benchmarks, exits, late dividends, valuation)
   is the `momentum` family's as H1 registered them.

What the look-ahead suites must see (backtest spec req 13): the existing truncation and
prefix invariance with the screen on; a revision case with teeth (a shares fact accepted
**after** close(T_i) that would move a name across the screen at T_i: `run(end=T_i)`
unchanged, `run(end=T_{i+1})` changed); a split-month case (a 2-for-1 with ex-date inside
the formation month leaves the name's turnover rank unchanged against a no-split twin);
and a pinned check that H1's reference metrics are unchanged with the key at its default
and that H1's, the T114 variants', B3's and `combined`'s canonical frozen sets and
fingerprints are unchanged **under the amended own-section rule** (above): the new key,
absent from their stored params, is left out of each canonical set at the default, while
B3's seven `profitability.*` keys at their defaults stay in B3's.

## Parameters

The block below is the only part the registry parses. It is **proposed**: the key
`strategy.turnover_top_fraction` does not exist in `StrategyConfig`, so registering this
file today fails on an unknown key. Every forbidden-prefix value is the `momentum` family's
rule as H1 registered it (strategy-lab spec, Definitions): the holdout `[2024-01-01,
2026-09-30]`, `in_sample_start = 2020-08-31`, 15 bp and H1's ladder, the ADR 0006
universe, `close` fills, the SIP feed. A `momentum` registration may not change any of
them, and may raise the cost base, never lower it; this file changes none.

```toml hypothesis
slug = "b10-short-term-momentum"
family = "momentum"
title = "B10: long-only short-term momentum, last month's winners in the top turnover quintile, top 20% equal weight, monthly"
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
turnover_top_fraction = 0.20

[schedule]
rebalance_cadence = "month_end"
signal_anchor = "month_end"

[costs]
per_side_bps = 15.0
commission_per_share = 0.0
commission_per_order = 0.0
sensitivity_per_side_bps = [0.0, 30.0, 60.0, 100.0]

[universe]
top_n_by_cap = 1000

[execution]
fill_price = "close"

[alpaca]
historical_feed = "sip"
```

Proposed answers, one line each (the owner confirms or changes them on #1358):

- **Cadence**: `month_end`, the evidence's cadence (SH-9 is a monthly sort with a one-month
  hold). `week_end` is pre-declared variant (i) below, not the first file: at a weekly
  cadence with `skip_months = 0` the month-end anchor can fall after T, which strategy-lab
  refusal 1(d) refuses, so the variant must carry `schedule.signal_anchor = "offset"`, a
  **rolling** one-month return read at each week-end, a different signal with a different
  turnover and cost profile that the paper does not report. `formation_months = 1` with
  `skip_months = 0` passes `StrategyConfig`'s rule (formation greater than skip); at
  `month_end` with the `month_end` anchor the anchor is T itself, so refusal 1(d) does not
  apply to this file.
- **Universe**: ADR 0006's, `top_n_by_cap = 1000`; the screen is a signal rule inside it,
  not a universe rule (ADR 0006 guard (a) is untouched: nothing here is a `universe.*`
  key, and the turnover screen is never tuned on P&L).
- **In-sample window under ADR 0016**: `in_sample_start = 2020-08-31` (the family rule,
  H1's since #975) to the development boundary **2023-12-29**, which for this family is
  also the last rebalance before `holdout.start`, so the window is H1's and B3's: 41
  rebalance sessions, 40 monthly returns. The family's holdout months were **spent by
  H1** (trial 4, #1301); B10 never reads them in sample (the boundary and the family rule
  forbid it) and never spends them (below).
- **Holdout and the paper book**: the family's, `[2024-01-01, 2026-09-30]`, spent. B10
  therefore has **no exam of record of its own** (ADR 0016 point 3: exams are for frozen
  finalists; the family's one spend went to H1, and this file pre-declares that B10 does
  not ask for a family repeat spend under req 5(d)). Its evidence is the in-sample trial,
  counted in `momentum`'s N, and the **paper book**: `momentum` is `paper_ready`, the
  family's `holdout.end` has passed, so under ADR 0017 B a book of its own (its own key
  pair and account) can open at the first month-end after `paper start`, as an ordinary
  Phase 4 tracking window, with the ADR 0005 tracking check reported per rebalance. That
  is exactly the "fast paper book" the owner asked for, and it is also the limit: a book's
  tracking check judges the machine against B10's own backtest, never B10 against H1, and
  under ADR 0017 F the strategy that goes live is chosen from exams of record, which B10
  does not have. If the owner wants B10 to carry an exam, B10-1's option (b) (a root
  family with a forward holdout, queued behind B9's) is the way, at the cost of a second
  queue slot and family code.
- **Costs**: the family's, 15 bp and `[0, 30, 60, 100]`; a 5 bp rung is **not** available
  to a `momentum` member (the ladder is a family rule), which is one argument in B10-1
  for a root family and the reason the report's break-even is read from the 30 bp rung's
  slope here.
- **Statistical threshold**: none (spec open question 10 (a)); the retirement condition is
  in words below.
- **Trades per month** (the report: one-sided turnover about 90%/month on about 40
  names): about **36 entries and 36 exits a month**, about 72 orders in the month-end
  run plus the equal-weight drift trades on the names that stay (few: about 4 of 40
  survive a month), well inside `risk.max_orders_per_run` (250).
- **Data each rule needs** (all held): rule 2, SIP bars with volume (ADR 0009, from
  2016-01-04), the shares fact (`facts`, `known_at` = acceptance, the universe's rule 7
  already reads it) and corporate actions (read-time adjustment); rules 1 and 4, the XNYS
  calendar and the adjusted price frame H1 reads; rule 3, nothing new. No vendor, no
  intraday data.

## Expected magnitudes and red flags

**No figure below is a long-only, top-1000, net number**, because the register holds none:
SH-9 is long-short and gross (or net of half-spreads, long-short). The prior is written to
be disconfirmable, with the arithmetic shown.

**Gross long leg.** Unknown. The largest-500 long-short spread is 0.42%/month (SH-9);
the share of a spread the long leg carries is unreported for this signal, and H1's file
quotes Israel & Moskowitz for momentum's long leg carrying about half, which is an
**unverified analogy** here as it was for B3. Taking half gives about **+0.2%/month, +2.4
pp/yr** over the average portfolio, gross, before any post-publication decay (QI-2 would
halve it).

**Costs at our scale, stated plainly.** One-sided turnover of about 90%/month (SH-9's own
figure) costs 2 × 90% × 15 bp ≈ **0.27%/month, about 3.2 pp/yr** at the base level, about
the whole gross long leg; at the 30 bp rung about 6.5 pp/yr. The report's verdict is this
sentence: at 15 bp the long-only book is about zero net at best, and positive only at
fills near 5 bp, which the family's ladder does not print. The net prior over SPY is
therefore **centred at about −1 pp/yr, plausible range −6 to +3 pp/yr**; at the HO-14
floor (5-10 bp plus half the spread) it is at or below zero.

**Tracking error against SPY.** Unknown; assumed **10%/yr** for the power arithmetic: 40
high-turnover names that just rose, re-chosen almost entirely each month, are noisier than
H1's decile (8.4%).

**Turnover and cost drag.** One-sided monthly turnover of about 85 to 95%; cost drag at
15 bp of roughly 3 to 3.5 pp/yr, at the 100 bp rung roughly 20 to 23 pp/yr. A `cost_drag`
far from ≈ 12 × `turnover_monthly` × 2 × `per_side_bps` is a cost-model bug.

**Losses.** A long-only book of recent winners in the most-traded names: a full-crisis
drawdown about the market's, and the momentum-crash shape H1's file describes (Daniel &
Moskowitz) can apply to a one-month sort too. No source in the register gives a worst
quarter for this construction; the run computes it.

**Red flags** (a prompt for a look-ahead and data audit, never a gate):

- Base-level `excess_cagr_spy` above `metrics.red_flag_excess_cagr_pp` (3.0): spec req 15
  marks the trial `red_flag`. A gross long-short spread of 0.42%/month does not support a
  net long-only excess of that size.
- One-sided `turnover_monthly` far below 70% or above 100%: the screen or the sort is not
  doing what rules 3 to 5 say (about 36 of 40 names should change each month).
- `n_screened` far from a fifth of `n_universe` (about 200), or `n_excluded_no_turnover`
  above a few dozen at any rebalance: the shares read or the volume frame has a hole.
- A name's turnover changing by the split ratio (×2, ×4, ×10, ×20) between adjacent
  rebalances around an ex-date: rule 2's adjustment failed, and the sort is the
  reversal book the report warns of.
- A result that changes when the run is truncated (truncation and prefix invariance), or
  a revision case (a late shares fact) that does not change the later run: look-ahead.
- H1's reference metrics moving when the key lands, or H1's canonical frozen set gaining
  the key at 1.0 under the amended own-section rule (the stored `hypothesis_fingerprints`
  row no longer matching the recomputed one): the default is not behaviour-preserving or
  the rule was not amended, and every `momentum` registration is at risk of an uncounted
  duplicate (a file repeating H1's strategy with the key written as 1.0 passing refusal
  1(b) as new).
- A survivorship gap above `gap.count_share_threshold` at any rebalance, a non-zero
  `n_static_listings`, or an `n_universe` below `universe.top_n_by_cap`: a biased or holed
  universe, as for H1 (its red-flag bullet says why).

## Power arithmetic

Rebalance sessions and monthly returns are H1's exactly (`tests/backtest/test_h1_file.py`
checks the counts): **41 rebalance sessions and 40 monthly returns** in sample
(2020-08-31 to 2023-12-29; the first rebalance fills on 2020-09-01, the 2023-12-29
rebalance fills on 2024-01-02, inside the spent holdout, so its month is not in sample).
There is no holdout run for B10 (above). A paper book of `paper.min_rebalances` (6)
month-ends is half a year.

**t-statistic** (ADR 0005: t ≈ SR × √years), at the assumed 10%/yr tracking error and, for
comparison, at H1's 8.4%:

| Window | Months | Years | t for +1 pp/yr at 10% TE | Excess needed for t ≈ 2 at 10% | Same at 8.4% TE |
|---|---|---|---|---|---|
| In-sample | 40 | 3.3 | **0.18** | **11 pp/yr** | 9.2 pp/yr |
| Paper book, 6 rebalances | 6 | 0.5 | 0.07 | 28 pp/yr | 24 pp/yr |

Any in-sample excess large enough to be significant is more than three times the red-flag
threshold, so it would read as a bug before it read as an edge. **The test cannot reach
significance for any result the prior allows.** What the in-sample run can show: whether
the screen and the sort are computed point-in-time (the revision and split cases), whether
turnover, the screened count and the book size sit inside the prior, the **sign** of the
screened one-month sort against its no-screen control (variant (ii)), and the slope of the
cost ladder, which the paper book then tests with real fills on about 72 orders a month.

**Deflated Sharpe** (spec req 8) is reported on both bases with N and V over the
`momentum` family's `ok`, non-synthetic, in-sample trials: H1's (trials 1 and 2 counted as
the spec counts them), the T114 sweep's six variants once run, and this one, at the
family's SR* high-water mark. H1's retirement reading was taken at its own N (T45b) and is
not reopened by this registration (strategy-lab spec refusal 1(c) sequenced it). This is
the price of the family choice, and it is the right price: a one-month momentum sort is a
look at momentum.

**Trial budget** (research-program §4 item 4; QI-11: every variant counted in
`momentum`'s N). Proposed, for the owner (B10-5): this registration (one sweep variant),
one run over the default in-sample window, no holdout spend, a paper book at the owner's
word, and at most **three pre-declared variants**, each a new sweep variant: (i)
`schedule.rebalance_cadence = week_end` **with `schedule.signal_anchor = "offset"`** (a
rolling one-month return read at each week-end, held a week: the faster book, at a
turnover the paper does not report; without the `offset` anchor strategy-lab refusal 1(d)
refuses it); (ii) `turnover_top_fraction = 1.0`
(**the control**: the plain one-month sort over the whole universe, which SH-1 says should
show no edge or reversal; its sign against this file's is the information the owner is
buying); (iii) `top_fraction = 0.10` (the decile of the screened set, about 20 names).
Not in the budget: a low-turnover reversal book (a short-horizon reversal is parked,
QI-17), a cap-weighted version (no `cap` weighting exists), a 5 bp cost rung (a family
rule). Reruns for logged bugs are counted as the spec counts them. Nothing else runs under
this file without a backlog entry and an owner decision.

## Prior-evidence disclosure

Every result for the family's holdout period (January 2024 to September 2026) already seen
before this draft.

**From the research report.** SH-9's evidence ends in 2018 (Medhat & Schmeling); the
report found no US test after it and did not compute a Chen & Zimmermann series for this
signal (STMOM is not in that data release). So no STMOM figure for 2019 onwards appears in
any report in `docs/research/`. The report's other CZ computations include 2024, after the
boundary, and the report says those months are never out-of-sample evidence for its
candidates; for B10 the point is moot, since the family's holdout is spent and B10 never
runs on it.

**Seen through the family.** H1's holdout spend (trial 4, #1301) is a `momentum`-family
result over exactly these months, in the registry, seen by the owner. Its benchmark facts
(MSCI USA 2024 +25.08% and 2025 +17.75%; Russell 1000 TR 2024 +24.51% and 2025 +17.37%;
MTUM 2024 +32.88% and 2025 +22.10%) are in H1's and B3's disclosures and describe the
period: a strong large-cap market led by technology, with momentum ahead of it. None of it
is a B10 result and none of it reaches a B10 run.

**General knowledge, owner and agents.** The owner follows the market. The agents that
wrote this file are language models whose training data covers market news through
mid-2026, including the published performance of short-term and high-turnover momentum
products; no figure from that memory is written here, because none can be cited, and
nothing in the design was tuned on it.

**Not seen.** No TradePartner trial with a turnover screen exists; no coverage or turnover
spike has been run (the first measurement of `n_screened`, the split cases and the shares
coverage is the engine task's fixture test and the in-sample trial itself).

## Retirement condition

Stated before any run, per spec open question 10 (a), and read the way H1's and B3's are.

**The reading is fixed in advance.** The condition reads two stored values of one trial:
the base-level `excess_cagr_spy` and the run-time `dsr_excess` in `trial_results` (N and
V at run time, over the `momentum` family). The trial is the **first** `ok`,
non-synthetic, `in_sample` trial of this hypothesis over the full default window
[2020-08-31, 2023-12-29] that passed `quant-auditor`. A later trial replaces it only when
an audit logged a bug in the earlier one (a look-ahead in the shares read, a split
adjustment defect, a screen defect). Shorter or later-start runs never count. The paper
book's tracking check neither retires nor promotes B10; it is reported.

**B10 retires as a candidate for live capital** (Phase 6) when that trial's net excess
CAGR over SPY is below **−1 pp/yr** **and** its `dsr_excess` is below 0.5: the same line
as H1, B3 and B9, so the files are read alike; each pp/yr is worth about t ≈ 0.18 here, so
the line is inside one standard error of zero. Retirement ends the line; a revisit is a
new hypothesis with its own budget (research-program §6). Note that `dsr_excess` for a
`momentum` member is deflated by the family's N, which the T114 sweep raises before this
file runs; that is intended (every look at momentum counts) and it makes the second half
of the rule easier to meet, not harder.

**The sign reading is pre-declared, and it is a diagnostic, not part of the rule.** The
`TP-` claim that records the result states the base excess of this file **beside** that
of the no-screen control (variant (ii), if run): same sign and the screen adds nothing;
opposite signs and SH-9's turnover mechanism is visible in our data; both negative and
SH-1's post-2001 null covers both. The comparison selects nothing (two variants of one
family, both counted); it labels the claim.

**Nothing promotes B10.** No result passes it, no DSR value is a threshold, and B10 has
no exam of record (above). A go-live decision needs an exam of record under ADR 0016
point 3, which B10 would need a root family to have (B10-1 (b)).

**A red-flagged trial neither retires nor promotes.** It opens a look-ahead and data
audit, and the hypothesis stays unresolved until the audit ends.

**A variant is a new hypothesis.** Changing any frozen value, including the screen
fraction, the formation window, the position fraction, the weighting or the cadence, is a
new sweep variant with a new slug, counted in `momentum`'s N. This file is never edited
after registration to fit a result.

## The family question (no code in this PR)

**Recommendation: a `momentum`-family amendment, one new frozen key.** The strategy-lab
spec's decided rule (open question 11 (a)) puts "a later block or a different world for
momentum" in a child family and "an unrelated signal" in a root family; a one-month total
return sort is neither: it is momentum's own signal at a different horizon, in the same
world (same window, costs, universe and fill convention), with one extra rule. The spec's
Out of scope paragraph names the mechanism for exactly this: a new `strategy.*` key by
spec amendment, with a `FROZEN_KEY_DEFAULTS` entry that preserves behaviour (`1.0`, no
screen) so no registration is re-registered. **One rule must change with it:** decision
13's own-section rule keeps every `strategy.*` key in a `momentum` canonical set even at
its default, so without an amendment the new key moves H1's, the T114 variants',
`oracle`'s and `combined`'s fingerprints (the first paragraph of this file says how); the
amendment carves out own-section keys added **after the family's first registration** at
their default (never the keys a family registered with: B3's `profitability.*` stay in
its set), or re-records the fingerprints under a data release. With that, the route needs no family code, no new N,
and no slot in the forward-exam queue, and it lets a later `momentum` sweep vary the
screen as an axis (`lab.sweepable_keys` gains the key by reviewed config change). Its
cost is stated above: B10 counts in momentum's N and has no exam of record of its own.

**What it would need** (estimates for the plan that follows an accepted spec amendment;
nothing is built here):

1. **A backtest spec amendment** (class B, `spec-critic`): the key
   `strategy.turnover_top_fraction` (a share in (0, 1], default 1.0, "the top fraction of
   the universe by formation-period share turnover that is ranked; 1.0 ranks everyone"),
   rule 2's turnover definition and its adjustment, the exclusion reason `no_turnover`, the
   counts `n_screened` and `n_excluded_no_turnover`, the look-ahead cases above, the
   `FROZEN_KEY_DEFAULTS` entry, **and the decision 13 amendment** (own-section keys
   added after the family's first registration left out of the canonical set at their
   default, keyed on the family's first-registration schema version or a marker, never on
   the lab baseline; or the re-record fallback), with the strategy-lab Definitions
   "Fingerprint" sentence pointed at it. Size S.
2. **Config and the canonical set:** `config.py` gains the key on `StrategyConfig`, the
   `FROZEN_KEY_DEFAULTS` entry, the reason and counts on `FAMILIES["momentum"]` (and on
   `oracle` and `combined`, which read the `strategy` section; `combined` applies the
   screen to its momentum half only if its spec says so, otherwise it ignores the key at
   its default), and optionally the `lab.sweepable_keys` entry; `backtest/frozen.py`
   `canonical_frozen_set` implements the amended own-section rule; `tests/test_config.py`
   and `tests/backtest/test_frozen.py` pin the default, and H1's, the T114 variants',
   **B3's** and `combined`'s canonical sets and fingerprints unchanged. Size S. `quant-auditor`,
   `safety-reviewer` (`config.py` is on both lists).
3. **The read and the screen:** `backtest/strategies.py` reads the formation month's
   volume from the bars frame, the shares fact through `facts_as_of(t)` and the actions
   the adjustment path already applies; `backtest/signals.py` applies the screen before
   the existing rank (a pure function over the frame, the shares and the fraction); the
   revision and split cases in `tests/lookahead/`; the engine, the schedule and the hold
   are unchanged. Size S to M. `quant-auditor`.
4. **The paper side:** `execution/strategies.py` and `execution/planning.py` read the same
   two inputs for the paper plan (the planner dispatches on the stored family and the
   frozen `strategy.*` values, so a `momentum` plan with the key at 0.20 screens and one
   at 1.0 does not). Size S. `safety-reviewer`, `quant-auditor`. The book itself needs
   ADR 0017 B (its own key pair and account); at `month_end` it needs nothing from ADR
   0017 C.
5. **The sweep file and registration** (owner): `docs/sweeps/b10-short-term-momentum.md`,
   one variant in `momentum`, after the T114 sweep has run (refusal 1(c) is already
   satisfied by H1's trials; the ordering after T114 is for a clean N, not a rule); then
   `sweep run`, the audit, and the promotion to this file.

## Open questions for the owner (B10-1 to B10-5; none decided)

- **B10-1. The family.** (a) `momentum` by amendment (above, recommended): one key, no new
  family, counts in momentum's N, no exam of record, paper book at once; (b) a new root
  family `short_term_momentum` with a forward holdout: its own N and exam, a 5 bp cost rung,
  but family code, and its holdout must not overlap B9's (the strategy-lab overlap rule),
  so one of the two queues behind the other for at least half a year. Recommendation: (a);
  the owner accepted this candidate for information and a fast book, not as a live
  candidate.
- **B10-2. The turnover measure.** (a) Formation-month share volume over shares
  outstanding known at `t`, split-adjusted (proposed; the paper's measure); (b) the
  universe's 20-session median dollar volume over market cap (already computed for rule 5,
  cheaper, but a different quantity: dollar turnover over a shorter window). Recommendation:
  (a).
- **B10-3. The fractions.** 0.20 × 0.20 (the paper's 5 × 5 corner, about 40 names) as
  proposed, or 0.20 × 0.10 (about 20 names). Recommendation: 0.20 × 0.20; the decile is
  variant (iii).
- **B10-4. The first cadence.** `month_end` first with `week_end` as variant (i)
  (proposed), or `week_end` first for the faster book. Recommendation: `month_end`; it is
  the evidence's cadence and the control (ii) is only meaningful beside it.
- **B10-5. The trial budget.** This file plus the three variants above, or a different set.
  Recommendation: as proposed, and run the control (ii) in the same sweep as this file so
  the sign reading has both halves at once.
