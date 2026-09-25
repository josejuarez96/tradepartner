# Hypothesis: H1, long-only 12-1 momentum, monthly

**Family:** momentum  ·  **Author:** team emory (agent draft); owner answers by Jose Juarez on #156  ·  **Date:** 2026-09-25

Merging this file does not register it. The owner runs
`tradepartner hypothesis register docs/hypotheses/h1-momentum-12-1.md` after merge (spec req
10, [backtest spec](../specs/backtest.md); plan T45b), **on or after 2026-10-01**, so that
`holdout.end` is a completed month-end. Before registering, the owner re-checks
`docs/research/` for holdout-period results published since this file was written and adds
them to the disclosure below; an edit before registration is not a new hypothesis. The
registry hashes the whole file, so any edit after registration makes a new hypothesis.

## Economic rationale

**The claim.** A long-only portfolio of the top tenth of the ADR 0006 universe by 12-1
momentum, equal-weighted and rebalanced monthly, earns about the market's return net of
costs. The prior for its excess return over SPY is centred on zero. That is not a claim of
an edge. The [backtest spec](../specs/backtest.md) says why the hypothesis runs anyway: the
point of Phase 3 is a correct, auditable engine, not a good number, and
[ADR 0005](../decisions/0005-objective-benchmark-stop-criteria.md) makes the benchmarks
instruments, never targets.

**Why the effect is worth testing.** The
[handoff](../research/2026-09-24-initial-research-handoff.md) grades H1 (cross-sectional
momentum, 12-1 month, monthly) SUPPORTED on these primary sources:

- Daniel & Moskowitz (JFE 2016): momentum has strong average returns across asset classes,
  with infrequent, severe crashes in panic states after market declines. The long-short
  portfolio lost −45.60% in March–April 2009.
- Novy-Marx & Velikov (RFS 2016, NBER w20721): 12-1 momentum was one of the few monthly
  anomalies to survive trading costs over 1963–2012, 0.68%/month net long-short (t 2.45),
  at 34.5% one-sided turnover per month and 0.65%/month of costs.
- Chen & Velikov, as cited by the handoff: size, value and momentum "performed well
  post-publication net of trading costs", consistent with Frazzini, Israel & Moskowitz.
- Israel & Moskowitz (JFE 2013): long positions carry about half of momentum profits, and
  momentum profits have no reliable relation with size. This is why a long-only, large and
  mid-cap version is worth a look at all.

Why the effect exists is contested. None of the repo's research reports grades an
explanation, so this file rests on the empirical record only.

**Why it may no longer exist.** [G1](../research/2026-09-25-g1-momentum-post-2010.md)
grades the post-2010, net-of-cost, long-only claim MIXED:

- The US long-short momentum factor fell from 0.92%/month (Jan 1987 – Jun 2002) to
  0.16%/month (Jul 2002 – Dec 2018), gross (Ben-David, Li, Rossi & Song, JFQA 2023).
- Momentum profits have been insignificant since the late 1990s (Bhattacharya, Li & Sonaer,
  RQFA 2017, to 2012).
- Returns are 58% lower after publication across 97 predictors (McLean & Pontiff, JF 2016).
- Typical mutual funds earn no returns to momentum after implementation costs (Patton &
  Weller, JFE 2020). The modern-era anomaly averages about 4 bp/month net (Chen & Velikov,
  JFQA 2023, abstract).
- Two live long-only large-cap momentum products (AQR Large Cap Momentum Style Fund; MTUM)
  earned between −2.79 and +1.09 pp/yr over the broad market across ten windows of five
  years or more between 2009 and 2025, net of their fees and trading costs. The four
  ten-year windows were +0.04, −0.27, −0.65 and +0.08 pp/yr.

**Design.** Formation 12 months, skip 1, monthly, per handoff H1 and
[ADR 0006](../decisions/0006-universe-and-cadence.md) (rebalance on the last XNYS session
of each month, one-month hold). The universe is ADR 0006's top 1000 by market cap with its
price, liquidity, history, shares and sector filters, rebuilt point-in-time. Top 10% of the
universe, equal weight (spec open question 1, answered below). No hysteresis band: a
buy/hold buffer raised net returns in Novy-Marx & Velikov (0.68 to 0.85%/month), but it is
a second hypothesis in this family, not a variant of this one. The signal uses total
returns because the H1 literature uses CRSP returns, which include distributions.

## Parameters

The block below is the only part the registry parses. It names every required key, and it
pins the three other frozen keys this file's prose relies on: `universe.top_n_by_cap`
(about 100 names at `top_fraction = 0.10`), `execution.fill_price` (`close`, the spec's
Definitions since T3) and `alpaca.historical_feed` (`sip`, consolidated volume for the
liquidity filter). Every other frozen key (`universe.*` except the size cut, `backtest.*`,
`adjust.*`, `master.*`, `gap.*`, `metrics.*`, `benchmarks`) takes its live config value at
registration and is printed with the rest, so the full frozen set is on record either way.

```toml hypothesis
slug = "h1-momentum-12-1"
family = "momentum"
title = "H1: long-only 12-1 momentum, top 10% equal weight, monthly"
in_sample_start = 2017-01-31

[holdout]
start = 2024-01-01
end = 2026-09-30

[strategy]
formation_months = 12
skip_months = 1
top_fraction = 0.10
weighting = "equal"
signal_total_return = true

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

Owner answers that set these values (spec open questions, answered on #156):

- **Q1, portfolio construction:** option (a), the top 10% of the universe, equal weight
  (`strategy.top_fraction = 0.10`, `strategy.weighting = "equal"`), about 100 names; no
  hysteresis band in H1. ADR 0006's revisit trigger fires with this choice: about $100 of
  live capital across about 100 names is about $1 per order, at Alpaca's fractional minimum.
  The owner accepts (a) for the backtest and records the trigger now for the Phase 4 spec,
  which must settle live sizing before any order.
- **Q2, holdout window:** option (a), `holdout.start = 2024-01-01` and
  `holdout.end = 2026-09-30`, the last completed month-end before an October 2026
  registration. Months after that are Phase 4 tracking, never holdout. The holdout is
  named here and never read from live settings.
- **Q8, `in_sample_start`:** option (a), `2017-01-31`, the first feasible rebalance on
  Alpaca history. Static-listing reliance (#35), dropped dividends and late dividends are
  reported per rebalance so the early-year bias stays visible. #101's probe
  ([report](../research/2026-09-25-alpaca-open-and-depth.md), "Probe 1 results") found
  Alpaca corporate actions complete back to 2016 within its scope: a ground truth of seven
  symbols (MNST, ISRG, AAPL, NVDA, GE, TSLA, KO), splits checked in 2016, 2017, 2020, 2021,
  2022 and 2024, spin-offs in 2019, 2023 and 2024, and AAPL and KO cash dividends (four a
  year) in every year 2016–2025. That narrows the corporate-actions worry behind option (b)
  for large caps; it says nothing about the smaller names in the universe.
- **Q10, statistical threshold:** option (a), no numeric cut-off. The retirement condition
  is stated in words at the end of this file. DSR is shown on both bases, never adjudicated
  (ADR 0005: beating a benchmark is never a phase-exit criterion).
- **Q3, costs** (not asked on #156; the spec's recommendation stands): `per_side_bps = 15`
  and the ladder `[0, 30, 60, 100]` are placeholders until Phase 4 paper fills recalibrate
  them. The 100 bp rung is about the per-dollar cost derived from G1's figures for
  Novy-Marx & Velikov: 0.65%/month ÷ (2 × 34.5%) ≈ 94 bp per side. Alpaca charges no
  equity commission.

The remaining `strategy.*` values are the spec defaults: formation 12 and skip 1 (handoff
H1), `signal_total_return = true` (the literature's CRSP total returns).

## Expected magnitudes and red flags

All figures are G1's, from its Tier 1 sources. None of the live products is a monthly
12-1, equal-weight, top-N portfolio: AQR combines several momentum signals with
cap-weighting and tax management; MSCI and MTUM use risk-adjusted 6- and 12-month
momentum, about 125 names, quarterly reconstitution and a turnover cap. So these are a
prior, not a forecast, and an equal-weighted book of about 100 names drawn from the top
1000 should sit further from SPY than MTUM does.

**Net excess return over SPY (base cost level).** Prior centred on **0 pp/yr**; plausible
ten-year range **−1 to +1 pp/yr**. Five-year windows of **−2.8 to +1.1 pp/yr** and
single-year gaps of **±13 to 18 pp** (MTUM 2021: −13.52 pp; 2023: −18.00 pp) occurred
live and count as normal. The largest gross index window found was +1.10 pp/yr.

**Tracking error against the broad market:** about **8.3–8.4%/yr** (MSCI factsheets,
Aug 31 2026; G1 does not state the period the figure covers). Expect ours at or above
that.

**Turnover and cost drag.** A plain monthly 12-1 decile turns over about **34.5% per side
per month** (Novy-Marx & Velikov). At 15 bp per side that is roughly 1.2 pp/yr of cost
drag; at the 100 bp rung roughly 8 pp/yr, about half the gross spread the literature
reports. Live products turned over 53–66%/yr (AQR) and 101–118%/yr (MSCI, MTUM) with
buffers; ours has none, so expect the academic figure, not the product figures.

**Losses.** Worst quarter about **−18 to −19%** (MTUM −18.70% in 2Q22; AQR −18.19% in
3Q11 and −17.97% in 1Q20); worst calendar year −18.23% (MTUM 2022). A full-crisis
drawdown of about **−55%**, about the same as the market's, is possible (MSCI, 2007–09,
back-tested). No Tier 1 source gives a post-2010 peak-to-trough; the run computes it.

**Red flags** (a prompt for a look-ahead and cost audit, never a gate):

- Base-level `excess_cagr_spy` above `metrics.red_flag_excess_cagr_pp` (3.0): spec req 15
  marks the trial `red_flag`. G1 found no live or gross window above +1.1 pp/yr.
- No loss of about −18% or worse in either of the two G1 worst-quarter episodes inside
  the window (1Q20, AQR −17.97%; 2Q22, MTUM −18.70%; the third, 3Q11, is before it).
  G1 reports no live momentum product escaping those quarters.
- Tracking error well below 8%/yr, or one-sided turnover far below 30%/month: the signal
  or the universe has collapsed (for example onto survivors or a handful of names).
- A `cost_drag` at the 15 bp level far from ≈ 12 × `turnover_monthly` × 2 × 15 bp (an
  approximation: compounding and the initial buy move it a little), or a net CAGR that
  rises with the cost level: a cost-model bug.
- A result that changes when the run is truncated (the spec's truncation and prefix
  invariance suites): look-ahead.
- A survivorship gap above `gap.count_share_threshold` at any rebalance, or a large
  static-listing count in 2017–2018: not a flag of an edge, but of a biased universe, and
  the holdout cannot be spent over it without a logged owner decision.

## Power arithmetic

Rebalance sessions are counted with `backtest.schedule.rebalance_sessions` on the XNYS
calendar; `tests/backtest/test_h1_file.py` checks the counts.

- **In-sample run:** the spec's default window, `in_sample_start` to the last rebalance
  session before `holdout.start`: 2017-01-31 to 2023-12-29. That is **84 rebalance
  sessions** and **83 monthly returns** (the first holding month is February 2017; the
  2023-12-29 rebalance fills on 2024-01-02, inside the holdout, so its month is not in
  sample). The spec's "~84 months" counts sessions; the arithmetic below uses 83 returns.
- **Holdout run**, pinned here so that January 2024 belongs to a window:
  `--start 2023-12-29 --end 2026-09-30`, from the last in-sample rebalance to
  `holdout.end`. The window overlaps the frozen holdout, so it needs `--spend-holdout` and
  runs as `kind=holdout` (spec req 11). That is **34 rebalance sessions** and **33 monthly
  returns**, January 2024 to September 2026. In-sample and holdout together cover February
  2017 to September 2026, 116 monthly returns, with no gap and no overlap.

**t-statistic** (handoff D1 and ADR 0005: t ≈ SR × √years, here the excess Sharpe, the
excess return over its tracking error):

| Window | Months | Years | t for +1 pp/yr at 8.4% tracking error | Excess needed for t ≈ 2 |
|---|---|---|---|---|
| In-sample | 83 | 6.9 | **0.31** | **6.4 pp/yr** |
| Holdout | 33 | 2.75 | **0.20** | 10.1 pp/yr |
| Both | 116 | 9.7 | 0.37 | 5.4 pp/yr |

A +1 pp/yr excess, the top of the prior range, would need about 280 years of data to
reach t ≈ 2. Any in-sample excess large enough to be significant (about +6 pp/yr) is
twice the red-flag threshold and above every live or gross window G1 found, so it would
read as a bug before it read as an edge. **The test cannot reach significance for any
result the prior allows.** That is why open question 10 takes option (a): a pass/fail
cut-off would be theatre. What the in-sample run can show is whether the engine is
correct (spec req 18, the oracle and the look-ahead suites) and whether the result sits
inside the G1 prior.

**Deflated Sharpe** (spec req 8) is reported on both bases. DSR = Φ((SR − SR*)·√(T−1) /
…), where SR* is the expected maximum Sharpe across the momentum family's `ok` in-sample
trials under no edge, from N (the trial count) and V (the variance of Sharpe over the
latest trial per distinct (parameter hash, window) pair). DSR below 0.5 means SR is below
SR*. With fewer than two distinct pairs, SR* = 0 and DSR reduces to PSR; only then does
DSR below 0.5 mean the Sharpe is more likely negative than positive. It is shown, not
adjudicated.

## Prior-evidence disclosure

Every result for the holdout period (January 2024 to September 2026) already seen before
registration. All were read by the research agents for G1 (#47), the handoff and the
free-data-terms report, and are quoted there; none was computed inside this system.

**From G1's sources:**

1. iShares MTUM fact sheet as of Jun 30 2026 (G1 source 2): calendar NAV returns 2024
   **+32.88%** and 2025 **+22.10%**, against its index **+33.16%** and **+22.33%**;
   annualised NAV to Jun 30 2026 of **43.70%** (1 yr), **34.58%** (3 yr), **15.94%**
   (5 yr), **17.58%** (10 yr) and **16.79%** since inception (Apr 16 2013); ten-year NAV
   17.58% against index 17.79%; 3-year equity beta **1.22** against the S&P 500; IT weight
   **53.22%**; 126 holdings. G1 also reports beta **1.28** on the iShares product page in
   September 2026.
2. MSCI USA Momentum SR Variant Index factsheet, Aug 31 2026 (G1 source 4), gross:
   2024 **33.16%** vs MSCI USA **25.08%** (+7.80 pp), 2025 **22.33%** vs **17.75%**
   (+4.35 pp); ten years to Aug 2026 **16.45%** vs **15.35%**; five years **11.95%** vs
   **12.32%**; tracking error **8.44%**; turnover **100.88%**.
3. MSCI USA Momentum Index factsheet, Aug 31 2026 (G1 source 5), gross: ten years to
   Aug 2026 **15.72%** vs **15.35%**; five years **11.38%** vs **12.32%**; tracking error
   **8.32%**.
4. iShares MTUM summary prospectus, 497K, Nov 28 2025 (G1 source 3): to Dec 31 2024,
   fund 1 / 5 / 10 yr **32.88% / 11.77% / 13.16%** vs MSCI USA **25.08% / 14.56% /
   13.08%** and the spliced SR Variant index **33.16% / 11.95% / 13.37%**; the ten-year
   window 2015–2024 is **+0.08 pp/yr** and the five-year window 2020–2024 **−2.79 pp/yr**.
5. AQR Large Cap Momentum Style Fund, 485BPOS FY2024 and 2026 (G1 source 6): to Dec 31
   2024, 1 / 5 / 10 yr **27.81% / 14.58% / 12.60%** vs Russell 1000 TR **24.51% / 14.28% /
   12.87%**; to Dec 31 2025, **15.88% / 12.54% / 13.94%** vs **17.37% / 13.59% / 14.59%**.
   Windows touching the holdout: 2015–2024 −0.27, 2020–2024 +0.30, 2016–2025 −0.65,
   2021–2025 −1.05 pp/yr. The fund became a multi-style fund in May 2026.
6. G1's own computations from those figures: the 2013–2025 and 2012–2025 gross index
   windows (+0.44 and +1.07 pp/yr) and the +1.10 pp/yr ten-year window to Aug 2026.

**From the handoff (H1 section):** the same MTUM 2024 and 2025 calendar-year returns and
the since-inception 16.79%, and a Tier 3, indicative-only comparison of MTUM's ten-year
return (~16.23%/yr) with SPY's (~14.98%/yr), correlation 0.86, as of mid-2026.

**From the free-data-terms report** (single bars, not results): the XOM 2024-05-16 bar
open 118.54 against the NYSE opening auction 118.89, and four SPY daily bars for
2024-01-02 to 2024-01-05 (volume 123,007,793 on 2024-01-02), both read as Alpaca feed
probes.

**Broad-market figures seen for the period:** MSCI USA 2024 +25.08%, 2025 +17.75%
(gross); Russell 1000 TR 2024 +24.51%, 2025 +17.37%. No SPY fact sheet is cited by any
report in `docs/research/`; the spec's "SPY 2024–26 fact sheet" was not read, and the
two indices above stand in for it.

**Not seen:** nothing in the other research reports touches strategy or benchmark returns
for the period. The 2024–2025 entries in the Alpaca depth report are corporate-action
probe facts (the NVDA 10-for-1 split of 2024-06-10, the GE to GEV spin-off of
2024-04-02), not returns. G1's unverified items (the MTUM −34.08% drawdown to March
2020) fall before the holdout.

**General knowledge, owner and agents.** The owner follows the market and knows the broad
shape of 2024–26: the large-cap technology run that a 12-1 top decile would have held for
much of the period, and its reversals. The agents that wrote this file and its sources are
language models whose training data covers market news through mid-2026. Nothing here was
tuned on either, but the holdout is not unseen in that sense.

**What this means.** The direction of the holdout period is already known: risk-adjusted
momentum beat the market by a wide margin in 2024 and 2025, on high beta and heavy IT
concentration, and its ten-year windows to mid-2026 turned positive. A holdout spend that
confirms this is weak evidence; one that contradicts it is informative about the design
gap between this portfolio and MTUM's. That is why the holdout counts as a process
rehearsal for Phase 4 tracking more than a test of an edge.

## Retirement condition

Stated before any run, per open question 10 (a).

**The reading is fixed in advance.** The condition reads two stored values of one trial:
the base-level `excess_cagr_spy` and the run-time `dsr_excess` in `trial_results` (N and V
at run time), not the page's recomputation with today's N and V. The trial is the **first**
`ok`, non-synthetic, `in_sample` trial of this hypothesis over the full default window
[2017-01-31, 2023-12-29] that passed `quant-auditor` (plan T45b). A later trial replaces
it only when an audit logged a bug in the earlier one. Shorter or later-start in-sample
runs never count. The holdout run neither retires nor promotes H1; its result is reported
only.

**H1 retires as a candidate for live capital** (Phase 6 of the
[roadmap](../roadmap.md)) when that trial's net excess CAGR over SPY is below −1 pp/yr
**and** its `dsr_excess` is below 0.5. It stays the Phase 4 paper vehicle either way: the
roadmap names the MVP strategy as the first paper strategy, and ADR 0005 makes Phase 4 a
test of process, not of returns.

**The −1 pp/yr line is a decision rule committed in advance, not a statistical finding.**
Each pp/yr of excess is worth about t ≈ 0.3 over the in-sample window, so −1 pp/yr is
inside one standard error of zero. The number is the bottom of G1's plausible ten-year
range applied to a seven-year window, which is not like for like; live five-year windows
as low as −2.8 pp/yr count as normal above. The rule retires H1 anyway, because its job is
to be a line drawn before the run, nothing more. The DSR half: `dsr_excess` below 0.5
means the excess Sharpe is below SR*, the expected maximum Sharpe across the family's
trials under no edge; with fewer than two distinct (parameter hash, window) pairs, SR* = 0
and it means the excess Sharpe is more likely negative than positive.

**Nothing promotes H1.** No result passes it, and no DSR value is a threshold. Whether it
goes live is a Phase 6 decision under ADR 0005, after the paper period and the ADR 0003
gap sign-off.

**A red-flagged trial neither retires nor promotes.** It opens a look-ahead and cost audit,
and the hypothesis stays unresolved until the audit ends.

**A variant is a new hypothesis.** Changing any frozen value, including adding a
hysteresis band or a different position count, is a new file with a new slug, counted in
the momentum family's N. This file is never edited after registration to fit a result.
