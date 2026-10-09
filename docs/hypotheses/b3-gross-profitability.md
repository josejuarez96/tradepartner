# Hypothesis: B3, long-only gross profitability (GP/A) tilt, monthly

**Family:** profitability (in `hypotheses.families` since T85)  ·  **Author:** team grossprof (agent draft on Fable 5.1, #720); owner answers B3-1 and B3-2 recorded 2026-10-03, B3-3 to B3-8 recorded 2026-10-04 (#811)  ·  **Date:** 2026-10-03  ·  **Amended:** 2026-10-04 (#851, `in_sample_start` follows H1's #842 amendment); 2026-10-04 (#811, B3-3 to B3-8 decided as recommended: no parameter value changes, B4 goes to a third family `combined`, the spanning diagnostic stays outside the retirement rule); 2026-10-06 (#1033, from the owner's decisions of 2026-10-06 on #720 and the read-only coverage spike: `in_sample_start` follows H1's #975 re-pin to 2020-08-31, closing #1002; the portfolio is about 60 names; what is scored and its tilt are disclosed; the stale-facts baseline is restated; the family amendment is accepted and its tasks T85 to T85f are in the backtest plan); 2026-10-07 (#1143, status text and the prior-evidence disclosure brought up to date before registration; no parameter, rule or count changed)  ·  **Status:** draft, registrable: T85 to T85e and T78 are merged (T85f registers and runs it)

Merging this file does not register it. Everything it needs is now on `main`: the
`profitability` family, its frozen keys and its signal (the
[backtest spec amendment of 2026-10-03, #720](../specs/backtest.md#amendment-2026-10-03-720-the-profitability-family-accepted-2026-10-06),
accepted 2026-10-06, built by plan tasks T85 to T85e in
[docs/plans/backtest.md](../plans/backtest.md), the last of them merged 2026-10-07 in
#1105), and the statement facts it reads, which reached the owner's store with plan task
T78 ([data-foundation spec](../specs/data-foundation.md), amendment #660; 2,134,064 rows,
on by default since #1139, 2026-10-07). The owner runs
`tradepartner hypothesis register docs/hypotheses/b3-gross-profitability.md` (plan task
T85f), on or after a date at which `holdout.end` is a completed month-end.
Before registering, the owner (who has answered every question at the end of this file:
B3-1 and B3-2 on #720, B3-3 to B3-8 on #811) confirms that `in_sample_start` and the
counts below still equal H1's file as registered (H1 registers first; this file followed
its #842 start on #851 and its #975 re-pin on #1033; if T116c's evidence or H1's own
pre-registration check moves H1's start again, this file follows by a new re-pin PR,
never ahead of it), re-checks `docs/research/` and the trial registry for
holdout-period results seen since this draft (H1's holdout spend included, since the
two windows coincide; the registry lists it as a `trials` row with `kind = 'holdout'`)
and adds them to the disclosure; an edit before registration is not a new hypothesis.
The registry hashes the whole file, so any edit after registration makes a new
hypothesis.

Backlog item [B3](../research/hypothesis-backlog.md#b3-profitability-tilt). Claims it
tests, by id in [claims.toml](../research/claims.toml): **QI-6** (SUPPORTED, narrowly) and
**QI-16** (MIXED). The research rules are in
[research-program.md](../ways-of-working/research-program.md).

## Economic rationale

**The claim.** A long-only portfolio of the top tenth of the ADR 0006 universe ranked by
**as-filed gross profit over total assets** (GP/A), equal-weighted and rebalanced monthly
on annual facts, earns about the market's return net of costs, with a prior for its
excess over SPY centred slightly above zero. As with H1, that is not a claim of an edge.
The point of the test is stated in the backlog: B3 is the best-evidenced stock-selection
idea in the register, it is the **first non-price signal**, and it proves the
fundamentals pipeline that B5 and the E1 economic test also need.

**Why the effect is worth testing.** QI-6, from the
[quantitative-investing brief](../research/2026-10-03-quantitative-investing-research-brief.md)
("End-of-Q2 survival table"), is the only SUPPORTED stock-selection claim in the
register: "slow, liquid, profitability-aware stock selection has an unusually substantial
foundation", scoped to *slow, liquid, diversified profitability tilts only; not every
quality bundle or short book*. The brief's SUPPORTED grade rests on independent T1
sources (Fama–French, Novy-Marx, Hou–Xue–Zhang, Jensen–Kelly–Pedersen), international
and pre/post-original out-of-sample tests, and two cost studies (Novy-Marx–Velikov;
institutional cost evidence). The brief's own sketch for a modest account (its "(a)
Individual with a modest account", item 2) is this signal: "rank latest as-filed gross
profits/total assets" inside "a contemporaneously investable large/mid-cap universe", and
it names the main failure modes: "restated accounting, sector bets or confusing the
market's return with tilt alpha". This file closes the first at the schema (first
vintage), **exposes** the second rather than bounding it (the brief's sketch has sector
and position bounds and cap-relative weights; this construction does not, see B3-5, and
its sector tilt is reported, not controlled), and measures the third with a pre-declared
spanning diagnostic (Retirement condition).

Why the effect exists is contested. The brief's Q1 table ("Economic mechanisms and the
other side") offers both a mispricing story (investors underweight durable earnings and
overpay for growth stories) and a rational one (discount rates and investment
opportunities link profitability to expected returns), and calls the causal risk
explanation unknown. No report in the repo grades an explanation; this file rests on the
empirical record only.

**Why it may be small or gone.** The register is explicit that the magnitude is unknown:

- QI-6's note: "pure modern all-cost magnitude unknown". The survival table's
  profitability row: gross premium "documented; exact audited magnitude unknown"; net
  "slow liquid evidence favorable; magnitude unknown"; post-2010 "OOS and recent
  retrospective; pure all-cost number unknown".
- QI-16 (MIXED): broader "quality" bundles overlap defensive and value exposures and are
  not established as distinct premia. This file therefore tests **plain gross
  profitability only**, no quality composite.
- QI-2 (UNGRADED, McLean & Pontiff): published anomalies lose about 58% of their return
  after publication; the brief warns against one universal haircut.
- G2-S10 (UNGRADED, Chen & Welch 2026): post-2005, non-microcap anomaly returns are
  near zero, a median of 7 bp/month (the claim's scope as stored: about 200 anomalies,
  top 3,000 stocks, post-2005; whether the figure is long-short or gross is not in the
  register).
- QI-18 (MIXED): a defensive portfolio may just repackage profitability and investment
  exposure, so a profitability tilt's return is partly a low-beta return in disguise;
  the backlog parks low-beta on the ground that B3 tests it.

**What the register does not give.** No report in `docs/research/` extracts a number for
the gross-profitability spread, for a long-only version of it, or for its turnover. The
brief's source 22 (Novy-Marx, *The Other Side of Value*) is the primary paper; its
figures are **not** in the register and are not cited here as support (research-program,
"How agents use this", item 3). Where this file needs a scale below, it says that it is a
placeholder and that the owner verifies it before registration.

**Design.** Annual fact, monthly rebalance, inside the ADR 0006 universe, the H1
construction (top 10% equal weight) so that B4 can later combine the two signals by
ranks (HO-10: weights fixed in advance, never fitted):

- **Signal at read time** `t = close(T)` for each rebalance session T (the last XNYS
  session of the month): for every universe member at `t`, the latest **annual**
  `gross_profit` row known at `t` and the `total_assets` row at the **same**
  `period_end`, both from `statement_facts_as_of(t)` (first vintage, acceptance-timed
  `known_at`; data-foundation spec req 16 and its amendment #660). Score = gross profit
  / total assets. Rank descending; ties by `security_id` ascending (spec req 3).
- **Annual** means `period_days` inside `profitability.annual_period_days` (default
  `[350, 380]`: calendar years, 52- and 53-week fiscal years). Quarters, half-years,
  nine-month year-to-date rows and fiscal-year-change stubs are ignored.
- **Fresh** means the session date of T minus `period_end` is at most
  `profitability.max_fact_age_days` (548, 18 months; decided B3-3 (a), #811).
- **Scope**: names whose SIC known at `t` lies in `profitability.exclude_sic_ranges`
  (`[[6000, 6999]]`, finance, insurance and real estate) are not scored (decided
  B3-4 (a), #811). They stay in the universe and in the gap report.
- **Portfolio**: the top `profitability.top_fraction` (0.10) of the scored names,
  `profitability.weighting = equal`, long-only, remainder cash, through the existing
  `portfolio.target_weights`. One-month hold. No hysteresis band (a second hypothesis in
  this family, counted in its N).
- Everything else (universe, fill convention, costs, benchmarks, exits, late dividends,
  valuation) is the backtest spec's as frozen for H1.

### Point-in-time rules, stated so the auditor can check them

The domain trap in this family is that an annual fact exists long before anyone can
read it. These rules are the hypothesis's, written here so the engine amendment and the
tests have one source:

1. **A fact is usable at `t` only from its filing's acceptance, and `t` is the
   session's official close.** `statement_facts` rows carry `known_at` = the filing's
   SEC acceptance timestamp (never the companyfacts `filed` date, never the period
   end), and `statement_facts_as_of(t)` returns rows with `known_at ≤ t` only, where
   `t = calendar.session_close(T)` (the boundary `known_at == t` is visible). A 10-K
   accepted after that instant on the rebalance session T is **invisible** at that
   read and enters the next month's read; one accepted before it is visible, as it was
   to anyone reading EDGAR before the close. The close is the calendar's, not 16:00 New
   York: two month-ends in the windows are half days (2024-11-29 and 2025-11-28, both in
   the holdout; close 13:00 New York; none in the in-sample window since the #975
   re-pin), where a 14:00 acceptance is invisible. The signal
   function takes `t` itself and filters `known_at ≤ t` again, so a frame that carries a
   later row (a fake provider in a test, a provider bug) cannot score it (amendment
   #720, "The signal"). No fact is ever pulled forward to its period end, and the engine
   never back-fills a step with a fact it learned later (one frame per step, spec req 2
   and req 13).
2. **The ratio's `known_at` is the later of its two rows'.** Gross profit and total
   assets may come from different filings (a derived gross profit from the 10-K, assets
   from the same 10-K in the common case; from a 10-K/A in a rare one). Because both
   rows are read as of `t`, the pair is usable only once both are known; the signal
   never mixes a known numerator with an unknown denominator.
3. **First vintage only, restatements invisible by construction.** The table holds the
   value each issuer **first filed** for a period and never a later restatement,
   reclassification or comparative column (#660 decision (d), UNIQUE on
   `(cik, fact_name, period_end, period_days)`). The signal therefore sees exactly what
   a reader of the filing saw, which is the brief's "restated accounting" failure mode
   closed at the schema. The price of that rule is accepted: a later correction of a
   wrong first number never reaches the signal either. The run's `statement_restated`
   count shows how much restatement the rule hides.
4. **Latest period wins, comparatives included.** A row whose first vintage came from a
   comparative column (`comparative = TRUE`: an IPO's first 10-K carrying two earlier
   fiscal years, for instance) is an ordinary row with that filing's acceptance as its
   `known_at`; at any `t` the annual row with the latest `period_end` is used, so a
   comparative is superseded the moment the filing's own year is known with it. Two
   annual rows with one `period_end` and different `period_days` (a 52-week and a
   53-week reading) take the larger `period_days`, deterministically.
5. **Missing, held, withheld and malformed facts exclude the name, never score it
   zero.** A member with no annual `gross_profit` row known at `t`, no `total_assets`
   row at that `period_end`, a stale pair, a non-positive or non-finite denominator or a
   non-finite numerator is **excluded from the ranking and counted** by reason
   (`n_excluded_no_facts`, `n_excluded_stale_facts`, `n_excluded_malformed`) on the
   rebalance row. That covers every empty key the ingest leaves on purpose: a
   conflict-withheld value (#660, pitfall P13), a key held behind an unstamped
   accession, an IFRS filer or a non-USD filer (`statement_none`, `statement_non_usd`),
   and a company whose income statement has no cost of revenue line (the coverage
   spike found about 107 such names per month-end in scope, V, MA, PYPL, DIS, XOM, MCD,
   CMCSA, UNP, UPS, LIN and CHTR among them; the tilt that leaves is disclosed under
   "What is scored" below). Exclusion is the only safe reading: zero would put every
   bank at the bottom and every untagged filer between the two tails.
6. **Derived gross profit is used and counted.** A `gross_profit` row with
   `basis = derived` (revenue minus cost of revenue from one filing, #660 decision (e))
   is the filing's own arithmetic and carries its acceptance, so it is as point-in-time
   as a reported row. `profitability.include_derived = true` keeps those names; the share
   of derived scores among the ranked names is reported per rebalance (`n_derived`), and
   excluding them is a pre-declared variant, not an edit (decided B3-6 `true`, #811).
7. **Where gross profit is undefined, the name is out of scope by rule, not by
   accident.** Banks, insurers and REITs report no cost of goods sold; some tag a
   `GrossProfit` anyway with a meaning the ratio cannot use. Rule 5 would exclude most of
   them as missing, but not all, and "most" is a sector bet taken by data quirk. The
   `exclude_sic_ranges` key makes the scope explicit and counted
   (`n_excluded_sector`). This is a signal-domain key under `profitability.*`, not the
   guarded `universe.exclude_sic_ranges` (ADR 0006 rule 3, a compliance rule that
   changes only by charter amendment); the universe, the top-1000 cut and the gap
   report are unchanged.
8. **Dual-class issuers.** `statement_facts_as_of` returns one row per listed class of
   a CIK (data-foundation spec req 8), so both classes carry one score and both may be
   admitted, as ADR 0006 rule 8 admits every listed class. Same as H1, where two classes
   carry near-identical momentum.
9. **The universe is still ADR 0006's, read at `t`**: shares, price, liquidity,
   history, sector and the cap cut use facts with `known_at ≤ t`; a filing lag in shares
   outstanding is rule 7's problem, not this signal's.
10. **No read-time derivations beyond the ratio.** No trailing twelve months, no fourth
    quarter from year-to-date rows, no calendarisation: #660 leaves those to Phase 3,
    and this hypothesis does not need them. A quarterly or TTM profitability signal is a
    different hypothesis in this family.

What the look-ahead suites must see for this family (backtest spec req 13, extended by
the amendment): truncation invariance and prefix invariance with the statement-fact
table in the truncated set, a revision case with teeth (a 10-K accepted **after**
`close(T_i)` whose annual row would change the ranking at T_i: `run(end=T_i)` unchanged,
`run(end=T_{i+1})` changed), and the plan-read timing check with the new plan fields
compared.

## Parameters

The block below is the only part the registry parses. It is **proposed**: the
`profitability` section, the family name and the per-family required-keys rule do not
exist in `backtest/hypothesis.py` or `config.py` yet (plan task T85; amendment #720, spec
open question 11, decided), so registering this file today fails on `family` and on
unknown keys. The
file does not name the inert `strategy.*` keys: the required sections are keyed per
family (B3-1, decided (a) by the owner, 2026-10-03). The three other frozen keys H1 pins
are pinned here for the same reasons (`universe.top_n_by_cap`, `execution.fill_price`,
`alpaca.historical_feed`); every other frozen key takes its live config value at
registration and is printed with the rest. The holdout, `in_sample_start`, costs,
universe and fill convention below are H1's so that the two families are one
data-and-cost world; B3 registers **before** the strategy lab's migration (B3-2, decided
(a) by the owner, 2026-10-03), so the lab's family rules, once they exist, are fixed from
this registration, and its overlap and standalone-file rules do not apply to it.

```toml hypothesis
slug = "b3-gross-profitability"
family = "profitability"
title = "B3: long-only gross profitability (GP/A) tilt, top 10% equal weight, monthly"
in_sample_start = 2020-08-31

[holdout]
start = 2024-01-01
end = 2026-09-30

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

[universe]
top_n_by_cap = 1000

[execution]
fill_price = "close"

[alpaca]
historical_feed = "sip"
```

Proposed answers, one line each (the owner confirms or changes them on #720; they mirror
H1's where the question is the same):

- **Portfolio construction** (decided B3-5 (a), #811): the H1 construction, top 10% of the
  scored names, equal weight. **About 60 names** (58 to 62 at every one of the 41
  month-ends 2020-08-31 to 2023-12-29 in the coverage spike: about 1,008 in the
  universe, about 212 out of scope as financials, about 185 in scope with no usable
  pair, about 610 scored; the draft's "70 to 90" assumed fewer holes). ADR 0006's
  revisit trigger fires harder than for H1: about $100 of live capital across 60 names
  is about $1.67 a position at the first buy, and every later rebalance trade (3 to 8%
  one-sided monthly turnover, the drift trades smaller still) is a fraction of that,
  below Alpaca's $1 fractional minimum per order; the backtest's `initial_capital` is
  scale-free, so nothing here changes, and the trigger stays recorded for the Phase 4
  spec.
- **Holdout window**: H1's, `[2024-01-01, 2026-09-30]`. Months after that are Phase 4
  tracking, never holdout. The holdout is named here and never read from live settings.
- **`in_sample_start`**: `2020-08-31`, H1's since its #975 re-pin (owner decision on
  #974, 2026-10-05); this file followed on #1033 (2026-10-06), as its own rule says and
  as #1002 asked to be checked first; it read `2019-11-29` (H1's #842 value, followed on
  #851) and `2017-01-31` (H1's original) before. The reasons are H1's and apply to B3
  unchanged, because B3 reads the same store through the same `universe_as_of`: the
  real-data dry run (#839) found the universe empty before mid-2019 (pre-2019 listings
  are `snapshot_static` rows, #35), and the 2026-10-05 audit (#974) found 192 established
  names with no bars before July 2019, so ADR 0006 rule 6 wrongly excluded about 19% of
  each 2019-11 to 2020-06 universe, a selection bias rather than a coverage ramp; from
  2020-08-31 the universe is the ADR 0006 top 1000 in substance. **The check #1002
  asked for, at both ends.** At the old start, H1's audit is B3's check: the bias is in
  `universe_as_of`, not in anything B3 adds, so it fails here as it failed for H1. At
  the new start, the coverage spike recomputed `universe_as_of` on the post-T117
  first-span store at all 41 month-ends from 2020-08-31 and found 1,004 to 1,009
  members at every one (the size cut binds), and H1's re-run of its own check there
  found 1,488 companies passing rules 1 to 7 with Healthpeak the one #974 name still
  out (#984, disclosed by H1 and inherited here). Statement facts exist in companyfacts
  from 2009 and Alpaca bars from 2016-01-04 (ADR 0009), so neither facts nor prices
  bind; listings did, exactly as for H1. The iXBRL phase-in rebalances (2019-11-29 to
  2020-07-31) are now outside the window, so the bias H1's Q8 disclosed for them does not
  reach this run. The first rebalance scores most December fiscal-year names on FY2019
  (its 10-K accepted in February or March 2020, about eight months old, well inside
  `max_fact_age_days`). The move drops the first quarter of 2020 from the in-sample
  run, as it did for H1, for the same data reason and with no result of any hypothesis
  on the real store consulted (the coverage spike read no price after any T and no
  return). If H1's start moves again, this file follows it, never ahead of it.
- **Costs**: the spec's placeholders, `per_side_bps = 15` and the ladder
  `[0, 30, 60, 100]`, as for H1, until Phase 4 paper fills recalibrate them; the
  strategy-lab family rules allow a higher base later, never a lower one.
- **Statistical threshold**: none (spec open question 10, option (a)). The retirement
  condition is stated in words at the end of this file; DSR is shown on both bases,
  never adjudicated.
- **Facts**: `gross_profit` and `total_assets`, two of the five names #660 ingests.
  Data-foundation open question 8 asked whether the first profitability hypothesis needs
  a name not on the list: **B3 itself does not**. The pre-declared cash variant below
  uses operating cash flow over assets, which needs only `operating_cash_flow`, already
  stored; that ratio is a **proxy**, not the cash-based operating profitability of
  Ball, Gerakos, Linnainmaa and Nikolaev (JFE 2016; #660 decision (a) names the paper),
  whose measure adjusts operating profitability for accruals and would need names the
  list does not carry (SG&A, working-capital changes). Neither is graded in any report.
  The proxy would be registered as `basis = "cash"` once the config literal gains the
  value; the paper's measure would reopen data-foundation open question 8. **Tag list
  (owner decision 2026-10-06, #720):** #660's five lists stand. The coverage spike found
  that `CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization` would rescue
  about 30 of the 183 uncovered CIKs (LIN among them) and `CostsAndExpenses` is tagged
  by 115 of them, but the first is ruled out by #660 on purpose and the second is total
  costs, a different concept; neither is added. Dimensioned product-and-service cost of
  revenue (the TMO shape) is unreachable from companyfacts by construction.

## Expected magnitudes and red flags

**No figure below is from a graded claim**, because the register holds none for this
construction (QI-6 note; survival table). They are a prior, written to be
disconfirmable, with the arithmetic shown so the owner can replace any input.

**Net excess return over SPY (base cost level).** Prior centred on **0 to +0.5
pp/yr**; plausible ten-year range **−2 to +2 pp/yr**. The arithmetic: G2-S10's median
post-2005 anomaly return of 7 bp/month (about 0.8 pp/yr; scope as stored: about 200
anomalies, top 3,000 stocks, post-2005) is the modern base rate; QI-6 puts
profitability among the few themes with favourable slow, liquid, cost-study evidence,
so take it above that median; a long-only top decile from the top 1000 keeps some share
of a long-short spread (the brief's "Long leg versus short leg": the split is unknown;
H1's file quotes Israel & Moskowitz for momentum's long leg carrying about half, and
nothing in the register says the same for profitability, so "half" is an unverified
analogy); costs at 15 bp per side on a low-turnover book take a few tenths of a point.
Single-year gaps against SPY of **±10 pp** are normal for a 60-name equal-weighted
book that is structurally light in asset-heavy sectors (utilities are out of the
universe by ADR 0006 rule 3, financials out of the ranking by scope, and energy and
telecoms rank low on GP/A while staying in) and heavy in asset-light ones (software,
pharmaceuticals, branded consumer goods); the gap is mostly sector weight, not stock
selection, which is the brief's "confusing the market's return with tilt alpha".

**Tracking error against SPY.** Unknown; assumed **6%/yr** for the power arithmetic,
below H1's 8.4% because the signal changes once a year per name and the book overlaps
SPY's large-cap growth weight. Expect ours between 5 and 9%/yr.

**Turnover and cost drag.** The signal is annual, so turnover comes from four sources:
new 10-Ks moving ranks (once a year per name, spread over the filing season), universe
churn at the cap cut, names entering or leaving scope (a stale pair, a new filer), and
equal-weight rebalancing of drift. Prior: **one-sided monthly turnover of 3 to 8%**,
with a seasonal peak at the **February, March and April** rebalances, when December
fiscal-year 10-Ks are accepted: the SEC deadlines are 60, 75 and 90 days after year end
for large accelerated, accelerated and non-accelerated filers (SEC rules, not register
evidence), and a top-1000-by-cap universe is almost all large accelerated filers, whose
10-Ks land in February and the first days of March. Cost drag at 15 bp of roughly 0.1
to 0.3 pp/yr, at the 100 bp rung roughly 0.7 to 2 pp/yr. The literature's low-turnover
classification of profitability (the brief's E5 row quotes Novy-Marx–Velikov's "50%
turnover per month" only as a threshold below which anomalies tend to survive costs) is
consistent with this but gives no number.

**Losses.** A long-only equity book: a full-crisis drawdown about the market's. No
source in the repo gives a worst quarter or a peak-to-trough for this construction; the
run computes it.

**Red flags** (a prompt for a look-ahead and data audit, never a gate):

- Base-level `excess_cagr_spy` above `metrics.red_flag_excess_cagr_pp` (3.0): spec req 15
  marks the trial `red_flag`. Nothing in the register supports a net long-only excess of
  that size.
- One-sided `turnover_monthly` above 10% on average, or any month above 25% outside the
  February–April filing season (the rebalances of 2024-02-29, 2024-03-28 and 2024-04-30
  and their counterparts in other years): the signal is flickering (a period band that
  misses 52/53 week years, pairs going stale and coming back, derived rows appearing and
  vanishing), not ranking.
- `n_ranked` below 70% of the in-scope names (`n_universe − n_excluded_sector`) at any
  rebalance, or `n_excluded_no_facts` rising over time: coverage, not selection
  (`statement_none`, IFRS filers, held keys, the tag lists being too short; #660 open
  questions 5 and 7). The coverage spike's baseline: 74.5 to 79.1% of in-scope names
  scored (589 to 627 names, 58.4 to 62.4% of the universe) at every month-end, no trend;
  `n_excluded_no_facts` 107 to 122. The draft's "below 60% of the universe" would have
  fired at about half the month-ends on the spike's numbers and was wrong, not the
  coverage.
- `n_excluded_stale_facts` **rising** at the February or March rebalance against
  January's: `max_fact_age_days` is too short for the filing calendar (B3-3, set at 548 to
  prevent this), and the book is shrinking and rebuilding every spring. Its **level** is
  not the flag: the spike counted 58 to 83 stale names per month-end, flat over the
  calendar month (in-scope coverage 75.6 to 77.5% in every month), and about 66 of them
  are issuers whose latest annual gross-profit row is more than two years old because
  they stopped tagging a cost-of-revenue subtotal (TMO, ORCL, INTU, DE, RTX, TMUS, T,
  BKNG, SBUX, GE: recent 10-Ks tag only `CostsAndExpenses`, or cost of revenue by a
  product-and-service axis that companyfacts drops), with about 5 genuinely late or
  irregular filers (549 to 730 days). Read the count as "about 70 abandoned tags plus a
  few late filers"; a February–March rise above the January level is the lag reading
  (the family spec keeps one count and no age split, its open question 16; T85f's
  evidence reads the split once from `statement_facts`).
- `n_derived` share jumping between adjacent rebalances: the derivation or the hold
  rule is unstable, and the ranked set with it. Baseline: 27 to 30% of scored names
  derived at every month-end in the spike, concentrated in the largest names (size
  decile 1: 31%; decile 10: 16%; AMZN is derived).
- A result that changes when the run is truncated (truncation and prefix invariance), or
  a revision case that does not change the later run: look-ahead, or a harness without
  teeth for this table.
- A `cost_drag` at 15 bp far from ≈ 12 × `turnover_monthly` × 2 × 15 bp, or a net CAGR
  that rises with the cost level: a cost-model bug.
- A survivorship gap above `gap.count_share_threshold` at any rebalance: a biased
  universe, as for H1. On the owner's store, a non-zero `n_static_listings` or an
  `n_universe` below `universe.top_n_by_cap` at any rebalance: as for H1, whose red-flag
  bullet says why the static count is zero by construction after #842 and why a short
  universe means the size cut did not bind (a coverage hole inside the window). The
  spike's recomputed universe held 1,004 to 1,009 members at every month-end from
  2020-08-31, so a count below 1,000 is a hole, not the norm.

### What is scored: coverage and tilt (read-only coverage spike, 2026-10-06)

The spike (`~/tradepartner-probes/b3-coverage/summary.md`; store opened read-only, no
price after any T and no return read, nothing in it is a trial) scored the rules above on
the SEC bulk `companyfacts.zip` with the adapter's acceptance stamps at all 41 month-ends
2020-08-31 to 2023-12-29, with the universe recomputed through `universe_as_of`. Per
month-end (range over the 41, then the mean):

| | range | mean |
|---|---|---|
| Universe (ADR 0006 top 1000) | 1,004 to 1,009 | 1,008 |
| Out of scope, SIC 6000–6999 | 200 to 222 | 212 (REITs, 6798: about 67) |
| In scope | 786 to 808 | 795 |
| **Scored (usable GP/A)** | **589 to 627** | **about 610** |
| of which reported `GrossProfit` | 417 to 453 | about 437 |
| of which derived (revenue − cost of revenue) | 163 to 187 | about 173 (28% of scored) |
| No facts (rules 2 and 3) | 107 to 122 | about 115 |
| Stale (> 548 days) | 58 to 83 | about 72 |
| Malformed (assets ≤ 0) | 0 | 0 |
| **Top 10%, the B3 book** | **58 to 62** | **about 60** |
| Scored / in scope; scored / universe | 74.5–79.1%; 58.4–62.4% | 76.5%; 60.5% |

Point in time: of 26,676 (gross profit, total assets) pairs used, **0** had either row
accepted after `close(T)`; period end to acceptance ran 41 to 60 days in a 30-fact sample,
and all 24,989 used numerators came from 10-K filings (20 from a 10-K/A), none from a
10-KT stub.

**The holes are a systematic slice, not noise.** The unscored in-scope names are the
payments networks (V, MA, PYPL), telecom and cable (T, TMUS, CMCSA, CHTR), rails and
parcel (UNP, UPS), integrated oil (XOM), restaurants (MCD, SBUX), DIS, LIN, and large
filers that dropped the gross-profit subtotal (TMO, ORCL, INTU, DE, RTX, BKNG, GE). By
SIC division, the share of member-months with a usable pair: manufacturing 88%, wholesale
89%, retail 84%, services 70%, construction 61%, mining and oil and gas 30%, transport,
communications and utilities 26% (all members, financials included, below). By size
decile of the universe (1 = the 100 largest): 70% in decile 1, 75% in decile 2, 79 to 82%
in deciles 3 to 5, 74 to 79% in deciles 6 to 10, so the largest decile is the worst
covered. Exchange makes no difference (NYSE 76%, NASDAQ 77%).

| SIC division | members per month-end | usable | derived | no facts | stale |
|---|---|---|---|---|---|
| 20–39 manufacturing | 396 | 0.88 | 0.20 | 0.06 | 0.06 |
| 70–89 services | 223 | 0.70 | 0.26 | 0.19 | 0.11 |
| 52–59 retail | 63 | 0.84 | 0.26 | 0.07 | 0.09 |
| 40–49 transport, communications, utilities | 42 | 0.26 | 0.18 | 0.53 | 0.21 |
| 10–14 mining, oil and gas | 29 | 0.30 | 0.24 | 0.54 | 0.16 |
| 50–51 wholesale | 25 | 0.89 | 0.04 | 0.00 | 0.11 |
| 15–17 construction | 16 | 0.61 | 0.12 | 0.29 | 0.10 |
| 60–67 financials (out of scope; shown for B3-4) | 212 | 0.19 | 0.14 | 0.77 | 0.04 |

**What that makes B3.** A test of gross profitability **among firms that report a cost of
revenue**, tilted away from mega-caps and from services, energy and telecom, on top of the
sector tilt GP/A has by construction (above). The gap is a selection the hypothesis
inherits from the data, as the iXBRL phase-in was for H1, and it is a scope statement for
this file, not a blocker (owner decision 2026-10-06, #720: proceed and disclose). Two
consequences are pre-declared here so they cannot be read into a result later: a
comparison of B3 with SPY is partly a comparison of "reports a cost of revenue" with the
whole large-cap market, and the spanning diagnostic (Retirement condition) is the tool
for that; and the cash variant (trial budget, (i)) scores about 99% of in-scope names
(778 to 805 per month-end), so it is the variant that tests the signal without the hole,
which is why the owner sent it to the backlog as a candidate follow-up (B3b).

## Power arithmetic

Rebalance sessions and monthly returns are H1's exactly, since the windows and the
calendar are the same (`tests/backtest/test_h1_file.py` checks the counts; plan task
T85's `tests/backtest/test_b3_file.py` pins them against H1's file): **41 rebalance
sessions and 40 monthly returns** in sample (2020-08-31 to 2023-12-29 since #975 and
#1033; 50 and 49 over 2019-11-29 to 2023-12-29 under #842 and #851; 84 and 83 over
2017-01-31 to 2023-12-29 before; the first rebalance fills on 2020-09-01 and the
2023-12-29 rebalance fills inside the holdout), **34 sessions and 33 returns** in the
holdout run (`--start 2023-12-29 --end 2026-09-30`, which needs `--spend-holdout` and
runs as `kind=holdout`), 73 returns together with no gap and no overlap.

**t-statistic** (ADR 0005: t ≈ SR × √years, the excess Sharpe being the excess return
over its tracking error), at the assumed 6%/yr tracking error and, for comparison, at
H1's 8.4%:

| Window | Months | Years | t for +1 pp/yr at 6% TE | Excess needed for t ≈ 2 at 6% | Same at 8.4% TE |
|---|---|---|---|---|---|
| In-sample | 40 | 3.3 | **0.30** | **6.6 pp/yr** | 9.2 pp/yr |
| Holdout | 33 | 2.75 | 0.28 | 7.2 pp/yr | 10.1 pp/yr |
| Both | 73 | 6.1 | 0.41 | 4.9 pp/yr | 6.8 pp/yr |

(Under #851 the in-sample row read 49 months, 4.1 years, t 0.34, 5.9 and 8.3 pp/yr, and
the Both row 82 months, 6.8 years, 0.44, 4.6 and 6.4 pp/yr; before #851, 83 months, 6.9
years, t 0.44, 4.6 and 6.4 pp/yr, and 116 months, 9.7 years, 0.52, 3.9 and 5.4 pp/yr.
Each shorter window weakens an already powerless test; none changes the conclusion
below.) A +0.5 pp/yr excess, the centre of the prior, would need about 580 years at 6%
tracking error to reach t ≈ 2. Any in-sample excess large enough to be significant (about
+6.6 pp/yr) is more than twice the red-flag threshold, so it would read as a bug before
it read as an edge. **The test cannot reach significance for any result the prior
allows.** What the in-sample run can show is whether the engine reads annual facts
correctly point-in-time (the look-ahead suites extended to this table), whether the
coverage and turnover sit inside the prior, and whether the result sits inside it.

**Deflated Sharpe** (spec req 8) is reported on both bases with N and V over the
`profitability` family's `ok`, non-synthetic, in-sample trials. This file's first trial
is the family's first, so N = 1 (plus any `return`-kind research runs the research
registry adds to N once #661 lands, its req 9), SR* = 0 and DSR reduces to PSR: DSR
below 0.5 then means the Sharpe is more likely negative than positive. The family's N
never includes H1's trials and H1's never includes this family's: the two signals are
unrelated and each family counts its own looks at the data (strategy-lab spec open
question 11, a root family).

**Trial budget** (research-program §4 item 4; QI-11: a dozen prespecified variants, all
counted). Set by the owner (B3-7 (a), 2026-10-04, #811): this registration, one
run over the default in-sample window and one holdout spend; **one spanning
diagnostic** per audited in-sample trial (the `return`-kind research run of the
Retirement condition, counted in this family's N under the research-registry spec); at
most **three pre-declared variants**, each a new file in this family and counted in its
N: (i) `basis = "cash"` (operating cash flow over assets, a proxy for cash-based
profitability, not Ball et al.'s measure; usable for about 99% of in-scope names in the
coverage spike, so it is the variant without B3's hole; the owner sent it to the backlog
as candidate B3b on 2026-10-06, drafted after B3 has run); (ii) `include_derived = false`
(reported gross profit only; the spike puts the derived share at 28% of scored names,
concentrated in the largest, so this variant changes the scored set materially); (iii)
the brief's construction, top third of the scored names, cap-weighted, if the strategy
lab adds `weighting = "cap"` (today `equal` is the only value). B3-4 (c), real estate
kept in scope, stays available but is low value: about 67 REITs are excluded per
month-end and only about 21 of them would score. B4 (momentum plus profitability by equal ranks) is a separate file, outside this
budget, in a **third family, `combined`** (B3-7 (b), decided 2026-10-04, #811): a root under the lab's
`FAMILY_PARENTS` with its own N, so B4's looks at both signals raise neither
`momentum`'s nor `profitability`'s N alone; the family enters `hypotheses.families` by
the reviewed code change that comes with B4's file, which is drafted only after B1 and
B3 have run. Reruns for logged bugs are counted as the spec counts them.
Nothing else runs in this family without a backlog entry and an owner decision.

## Prior-evidence disclosure

Every result for the holdout period (January 2024 to September 2026) already seen before
this draft. **To be completed by the owner before registration**, in particular with any
H1 holdout spend (same window, same universe, same benchmarks) and any fund fact sheet
read after this date.

**From the research reports.** None of the reports in `docs/research/` quotes a 2024–2026
return for a gross-profitability or quality portfolio, index or fund; the
[brief](../research/2026-10-03-quantitative-investing-research-brief.md) states every
profitability magnitude as "unknown" and cites no product. The claims pilot's HS-4 sketch
(the origin of this item) carries no number.

**Benchmark-period facts already seen through H1's disclosure** (they apply to this
holdout unchanged): MSCI USA 2024 **+25.08%** and 2025 **+17.75%** (gross); Russell 1000
TR 2024 **+24.51%** and 2025 **+17.37%**; MTUM 2024 **+32.88%** and 2025 **+22.10%** NAV,
3-year beta 1.22, IT weight 53%; MSCI USA Momentum SR Variant 2024 +33.16% and 2025
+22.33%. The direction of the holdout for the **benchmarks** is therefore known: a strong
large-cap market led by technology, with momentum well ahead of it.

**What that implies for this book.** A GP/A top decile from the top 1000 is structurally
heavy in asset-light sectors, and over 2024–2025 that overlap with the leading sector
is more likely than not to have helped it against SPY, and to have been well short of
MTUM. None of that is a measured result; it is the agents' and the owner's reading of a
period they lived through.

**General knowledge, owner and agents.** The owner follows the market and knows the broad
shape of 2024–26. The agents that wrote this file and its sources are language models
whose training data covers market news through mid-2026, including the published
performance of quality and profitability factor products over the period; no figure
from that memory is written here, because none can be cited, and nothing in the design
was tuned on it. The holdout is not unseen in that sense.

**Not seen.** No TradePartner trial of this family has run in the owner's store as of
2026-10-07; the registry holds H1's in-sample trials only (T45b, 2026-10-06), no holdout
spend of any family (H1's holdout waits on gates #1017 and #1018). The 2026-10-06 coverage spike opened the store read-only, read no price after
any rebalance T and computed no return, so it is coverage data, not a result. The 2024–2025 entries in the Alpaca depth report are
corporate-action probe facts, not returns. T78's real-store evidence (coverage at a
month-end T, one hand-checked issuer's FY revenue, cost of revenue, gross profit and
total assets against its 10-K) is data, not a result, and has been seen: coverage of
968 to 971 of 1,008 at 2021-10-29, 2022-11-30 and 2023-10-31, and Apple's FY2022 10-K
(revenue 394,328M, cost of revenue 223,546M, gross profit 170,782M, total assets
352,755M), both in PR #1139.

**Dry run on a scratch copy, 2026-10-07.** To check that registration and the in-sample
run work end to end before the owner's T85f, an agent session copied the owner's store
to a scratch file and ran `hypothesis register` and `backtest b3-gross-profitability
--start 2020-08-31 --end 2023-12-29` against the copy, this file unchanged. That trial
exists only in the copy's registry, not the owner's. It is an in-sample result over
this file's own window, not a holdout one; treat its output as seen. No parameter,
rule, count or threshold in this file changed after it: #1143's edits are the status
text and this disclosure only.

**What this means.** A holdout spend that shows a positive excess over SPY and a large
shortfall against MTUM confirms what the period's benchmark facts already suggest and is
weak evidence of anything; one that shows the opposite is informative about the gap
between this construction and the sector story. As for H1, the holdout counts as a
process rehearsal more than a test of an edge.

## Retirement condition

Stated before any run, per spec open question 10 (a), and read the way H1's is.

**The reading is fixed in advance.** The condition reads two stored values of one trial:
the base-level `excess_cagr_spy` and the run-time `dsr_excess` in `trial_results` (N and
V at run time), not the page's recomputation with today's N and V. The trial is the
**first** `ok`, non-synthetic, `in_sample` trial of this hypothesis over the full default
window [2020-08-31, 2023-12-29] (the `in_sample_start` of #975 and #1033) that passed
`quant-auditor`. A later trial replaces it
only when an audit logged a bug in the earlier one (a look-ahead in the fact read, a
period-band or staleness defect, a derivation defect). Shorter or later-start in-sample
runs never count. The holdout run neither retires nor promotes B3; its result is
reported only.

**B3 retires as a candidate for live capital** (Phase 6 of the roadmap) when that trial's
net excess CAGR over SPY is below **−1 pp/yr** **and** its `dsr_excess` is below 0.5.
Unlike H1, B3 is not the Phase 4 paper vehicle, so retirement ends its line: a later
revisit is a new hypothesis with its own registration and budget (research-program §6).

**The −1 pp/yr line is a decision rule committed in advance, not a statistical
finding.** Each pp/yr of excess is worth about t ≈ 0.30 over the in-sample window at the
assumed tracking error (0.34 under #851, 0.44 before it), so −1 pp/yr is inside one
standard error of zero and inside the prior's range. It is the same line H1 drew, chosen so the two families are read alike;
its job is to be a line drawn before the run.

**The backlog's second kill criterion is a diagnostic, not part of this rule.** The
backlog says B3 is killed when its excess is "explained away by momentum and market
exposure". The engine stores no spanning regression (req 7's metric keys are fixed), so
that reading cannot be committed to a stored value here. It is operationalised as a
pre-declared **diagnostic**, specified now so it cannot be re-specified after a look:
a regression of B3's monthly returns (over cash; `metrics.risk_free_rate = 0`, so the
raw monthly returns) on **SPY's monthly returns and MTUM's monthly returns in excess of
SPY's, jointly**, from the three `trial_equity` series of the retirement trial; reported:
the intercept, its t-statistic and both loadings. Regressing B3's excess over SPY on
MTUM's excess over SPY alone would force B3's market beta to 1 and put a low-beta tilt
(QI-18) into the alpha, the brief's "confusing the market's return with tilt alpha".
It runs on the **in-sample retirement trial only**, never on a holdout trial. Under the
research-registry spec (its open questions decided on #810) that computation reads returns and is a
`return`-kind run in the `profitability` family, registered before it runs and counted
in N (its req 9; it is in the trial budget above). Until the registry exists it is not
run. It reads the retirement trial's `trial_equity` series through `load_dataset` as
an ordinary dataset version that a reviewed dataset-build step exports (that spec's open
question 7, decided (d) by the owner on 2026-10-04, #810; its req 11; #728 filed the
question). Its result informs the
B4 decision (whether the two signals are worth combining) and the backlog's next
ranking, never this retirement. It is not part of the retirement rule and adds no
`metrics` key (B3-8, decided 2026-10-04, #811).

**Nothing promotes B3.** No result passes it, and no DSR value is a threshold. Whether it
goes to paper or live is an owner decision under ADR 0005 after H1's paper period and the
ADR 0003 gap sign-off; the paper spec accepts one hypothesis per window.

**A red-flagged trial neither retires nor promotes.** It opens a look-ahead and data
audit, and the hypothesis stays unresolved until the audit ends.

**A variant is a new hypothesis.** Changing any frozen value, including `basis`,
`include_derived`, the period band, the age bound, the sector scope, the position count
or the weighting, is a new file with a new slug, counted in the profitability family's
N. This file is never edited after registration to fit a result.

## Open questions for the owner (B3-1 and B3-2 decided 2026-10-03 on #720; B3-3 to B3-8 decided 2026-10-04 on #811; none open)

- **B3-1. How the family's keys enter the file.** Decided (a), owner, 2026-10-03 (with
  spec open question 11): a new frozen section `profitability` required in full when
  `family = "profitability"`, the required sections keyed per family (`momentum`:
  `strategy`; `profitability`: `profitability`; `costs` always), as the block above
  assumes; the file never names the inert `strategy.*` keys. The section lands with a
  defaults mechanism (the strategy-lab spec's `FROZEN_KEY_DEFAULTS` and `frozen_values`,
  or a minimal equivalent in T85; T96 merged as #1021 on 2026-10-06, so plan task T85
  builds on it), because adding a frozen section today makes H1's
  registration unrunnable until re-registered, which the lab spec forbids (amendment
  #720, "Frozen keys").
- **B3-2. Registering B3 beside the strategy lab.** Decided (a), owner, 2026-10-03
  (with spec open question 13): B3 is registered **before** the lab migration, as a
  pre-lab registration, this file as written, under the Phase 3 rules for its runs and
  its one spend, grandfathered by `pre_lab_hypotheses`; the plan orders T85f before the
  lab's migration task (the strategy-lab plan was not written then; it is #933 now). So neither the
  lab's holdout-overlap rule nor its standalone-file refusal applies to B3, and the one
  lab-spec sentence this draft changes is the fingerprint one (`family` and the family's
  own section in the fingerprint). Rejected: re-casting the file as a one-value sweep
  plus a promotion, with the overlap rule amended in both directions; a holdout from
  2026-10-01. A shared window still means two families can spend one holdout: the
  amendment marks a spend whose window overlaps another family's recorded spend
  (`holdout_seen_family`, reported, not a gate), an H1 holdout result is evidence this
  file must disclose at registration (above), and ADR 0005 means no holdout result
  selects between the two families. **Superseded in part, 2026-10-06 (#1033, spec open
  question 15):** the `holdout_seen_family` mark was dropped from the accepted amendment;
  the overlap is a query over `trials` (`kind = 'holdout'`, the window columns, the
  family through `hypotheses`), and the disclosure duty and the ADR 0005 rule stand. The
  strategy-lab plan has since been written (#933); T85f is ordered before its T113.
- **B3-3. `max_fact_age_days`.** The filing calendar (SEC deadlines, not register
  evidence): a 10-K is due 60, 75 or 90 days after fiscal year end for large
  accelerated, accelerated and non-accelerated filers, so a December fiscal year's
  10-K lands between mid-February and the end of March, and at the February month-end
  most filers due about 1 March have not filed yet, when their prior year's row (period
  end 2023-12-31 at the 2025-02-28 rebalance, say) is 425 days old. Options: (a) 548,
  an engineering bound of one year plus six months: every timely filer stays scored on
  last year's row until this year's 10-K is accepted, including a 90-day filer's 455
  days; (b) 455 (one year plus the longest 10-K deadline; a late filer drops out, which
  is arguably information); (c) 400, ADR 0006 rule 7's bound for shares outstanding,
  which excludes the filers due in March at every February month-end and rebuilds the
  book each spring. Recommendation: (a); (c) is wrong for annual facts. No option is a
  literature convention: the Fama–French June-to-June alignment is a different device
  (a fixed six-to-eighteen-month lag) that acceptance-timed facts do not need.
  **Decided (a), owner, 2026-10-04 (#811):** 548, as the block above already reads.
- **B3-4. Sector scope.** Options: (a) `profitability.exclude_sic_ranges = [[6000,
  6999]]` as a frozen signal key (above); (b) no scope key, financials excluded only
  when their facts are missing (rule 5), accepting that a bank with a tagged
  `GrossProfit` is ranked on it; (c) the narrower `[[6000, 6499]]` (depository, non-
  depository, brokers, insurance) keeping real estate (6500–6799) in scope, since REITs
  do tag revenue and cost of revenue. Recommendation: (a) for the first test, (c) as a
  variant if coverage shows REITs are a large share of the exclusions. Also to confirm:
  this key is **not** the guarded `universe.exclude_sic_ranges` and needs no charter
  amendment, because it changes what is scored, not what may be traded.
  **Decided (a), owner, 2026-10-04 (#811):** `[[6000, 6999]]` as a frozen signal key,
  not the guarded `universe.*` key, no charter amendment; (c) stays a pre-declared
  variant if coverage shows REITs are a large share of the exclusions. Coverage spike
  2026-10-06: REITs are about 67 of the about 212 names excluded per month-end (32%),
  but only about 21 would score (31% of REIT member-months have a usable pair, mostly
  derived), so (c) is cheap and low value; the owner kept (a) (#720, 2026-10-06).
- **B3-5. Construction.** Options: (a) the H1 construction, top 10% of scored names,
  equal weight (above; B4-compatible; ADR 0006's small-order trigger already recorded);
  (b) the brief's sketch, top third of scored names cap-weighted (closer to the
  literature's value-weighted portfolios; needs a `weighting = "cap"` value and a cap
  read the engine does not do today); (c) top 50 equal weight. Recommendation: (a) now;
  (b) as the strategy lab's first `profitability` sweep axis once `cap` exists.
  **Decided (a), owner, 2026-10-04 (#811):** top 10% of scored names, equal weight.
- **B3-6. Derived gross profit.** `include_derived = true` (above) or `false`.
  Recommendation: `true`, with the derived share reported; excluding derived rows would
  drop every filer that reports cost of sales but no gross-profit subtotal, a sector-
  shaped hole, and the variant tests the difference if the share is large.
  **Decided `true`, owner, 2026-10-04 (#811):** derived rows are scored and their share
  is reported per rebalance (`n_derived`). Coverage spike 2026-10-06: 28% of scored
  names are derived (163 to 187 per month-end), concentrated in the largest names (size
  decile 1: 31%, decile 10: 16%; AMZN is derived), so `false` would change the scored
  set materially; the variant is worth running.
- **B3-7. Family trial budget and where B4 lives.** (a) The budget: this file plus at
  most three pre-declared variants (above), or a different number; the strategy-lab
  spec's `lab.max_variants_per_sweep` and family caps apply on top once it lands. (b)
  B4's family: `profitability` (a child construction of this signal), `momentum` (it
  holds momentum's data), or a third family `combined` (its own N, a root under the
  lab's `FAMILY_PARENTS`). Recommendation: (a) as proposed; (b) a third family, since
  B4's trials are looks at both signals and should raise neither family's N alone while
  being counted somewhere; the owner may prefer to defer (b) until B1 and B3 have run.
  **Decided, owner, 2026-10-04 (#811):** (a) as proposed, this file plus at most three
  pre-declared variants; (b) a third family `combined`, a root under `FAMILY_PARENTS`
  with its own N. The family is created by the reviewed code change that lands with
  B4's file, and B4 is drafted only after B1 and B3 have run, so nothing is built now.
- **B3-8. The spanning diagnostic.** Keep it a registered research run outside the
  retirement rule (above), or add a `metrics` key (`alpha_vs_mtum_monthly`, its t) by
  spec amendment so the rule can read it. Recommendation: keep it outside; a stored
  regression on 40 months (49 under #851) has no power either, and the rule stays readable from two
  stored values.
  **Decided outside, owner, 2026-10-04 (#811):** the diagnostic stays a registered
  `return`-kind research run outside the retirement rule; no `metrics` key is added. Its
  home is the research-registry spec's open question 7 (d), decided the same day (#810):
  the retirement trial's `trial_equity` series is exported as an ordinary dataset version
  and the run reads it through `load_dataset`.
