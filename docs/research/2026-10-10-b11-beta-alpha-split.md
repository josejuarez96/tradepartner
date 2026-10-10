# Research Report: B11, the beta/alpha split of every logged long-only trial

**Brief:** #1461 (B11 of [beyond long-only](2026-10-10-beyond-long-only.md), owner approval in #1452)  ·  **Date:** 2026-10-10  ·  **Status:** COMPLETE  ·  **Agent/model:** team betasplit, claude-opus-5-5; read-only analysis on the owner's store

**Question.** Every long-only trial we have logged trailed SPY in-sample, and H1's spent holdout beat it. Did those results come from stock picking (alpha) or from market exposure (beta)? And what would a book that shorts its trailing beta in SPY have earned against T-bills (LS-13 to LS-15)?

## Answer
**Verdict:** the in-sample losses are **not explained by market exposure**: every in-sample beta is 0.96 or higher, so beta added to the return, and the arithmetic shortfall against SPY sits in the alpha term (stock picking, plus any tilt CAPM cannot see). H1's holdout win came from **market exposure**: it is not explained by stock picking. No trial shows alpha distinguishable from zero (every |t| < 1). A beta-hedged version of every in-sample book lost money against T-bills. LS-15 (a hedged B3 beats T-bills) is NOT SUPPORTED on the in-sample window.  ·  **Confidence:** high that market exposure did not cause the in-sample losses (the beta term is ≥ −0.4 pp/yr everywhere); low for the size of any alpha (40 months, all |t| < 1). The split is of the arithmetic mean active return, not the CAGR gap. For the concentrated top-5% books (trials 12, 14, 15) half or more of the CAGR gap is variance drag from 21 to 25% tracking error, and their alpha t is about −0.1 to −0.3, so for them the honest reading is "not beta", not "negative alpha".

In plain words:
- **H1 in-sample (trial 2):** H1 trailed SPY by 4.1 pp/yr. Its beta was 1.04, so market exposure added +0.4 pp/yr. Stock picking cost −4.5 pp/yr. The six T114 variants match this: alpha −1.5 to −4.9 pp/yr, beta 0.96 to 1.18, and the beta term between −0.4 and +1.8 pp/yr. **Momentum did not lose by market exposure; the shortfall sits in alpha (stock picking or tilts CAPM cannot see).**
- **B3 (trial 3):** trailed SPY by 2.4 pp/yr. It is close to an index (R² 0.84, beta 1.08). Its alpha was −3.2 pp/yr and the beta term +0.7.
- **H1 holdout (trial 4, already spent; stored equity only):** H1 beat SPY by 7.2 pp/yr, but its monthly beta was 1.92 (daily 1.63) in a market that rose 15 pp/yr over bills. The beta term was +13.8 pp/yr and the alpha −6.6 pp/yr. **The holdout "win" was leverage to a rising market, not momentum skill.** Beta above 1 is significant (t = 3.3 monthly).
- **The hedged line:** shorting the trailing, point-in-time beta in SPY left every in-sample book 8 to 17 pp/yr below T-bills. That held from April 2021 to December 2023, the months where a trailing beta exists. With the hedge ratio capped at 1 (Reg T), the shortfall is 8 to 13 pp/yr. The hedge strips out the market and leaves the negative alpha, as LS-13's arithmetic says it must.

**What it implies for B12.**
- A SPY hedge cannot rescue a long book whose alpha is negative. So a hedged-long momentum or B3 book is not worth a run on this evidence (step 6 of the beyond-long-only report stays "INSUFFICIENT as a strategy").
- B12's question is still open: does a long-short book earn what the long-only book did not? But B12 has to start from this result. The long leg has given negative alpha against SPY in every logged variant, so any positive B12 result must come from the short leg or from the spread.
- The CAPM alpha here is measured against a cap-weighted index. The trials are equal-weighted, so part of the negative "alpha" may be a size tilt rather than a stock-selection failure (equal-weight lagged cap-weight sharply in 2023; UNVERIFIED here, see below). B12's equal-weighted-universe control is therefore needed, not optional. Without it, B12 cannot tell a momentum failure from a size bet.
- B12 also needs the T-bill proxy. This report used FRED DTB3. B12 should store a proxy with `known_at` (step 0 of the beyond-long-only report), not fetch it ad hoc.

## Method
- **Data.** The owner's store, opened with `duckdb.connect(..., read_only=True)` on 2026-10-10. The `trial_equity` rows were `series in ('strategy', 'SPY')` at `cost_per_side_bps = 15` (the registered headline cost). SPY is the engine's benchmark book on the same sessions; it is valued on the adjusted frame, so dividends are included (its stored CAGR, 11.1% over 2020-08-31..2023-12-29, is SPY's total return). Nothing was written to the store or the trial registry, and no trial was run. Trial 4's numbers come only from its stored equity; nothing new was read from holdout-period store data.
- **Trials.** These are all ten trials with equity in the store. Trials 5 to 10 have no `trial_equity` rows.

  | Trial | Book |
  |---|---|
  | 1, 2 | H1 in-sample |
  | 3 | B3 GP/A in-sample |
  | 4 | H1 holdout |
  | 11 | v1: top 20%, daily |
  | 12 | v2: top 5%, daily |
  | 13 | v3: top 20%, month-end |
  | 14 | v5: top 5%, month-end |
  | 15 | v4: top 5%, week-end |
  | 16 | v6: top 20%, week-end |

  Trials 11 to 16 are the T114 sweep variants (hypotheses 3 to 8).
- **T-bill proxy.** FRED DTB3, the 3-month T-bill secondary-market discount rate, was fetched once on 2026-10-10. The store holds no BIL or SGOV bars; `listings` has no such ticker. A session's T-bill accrual is DTB3 × calendar days / 360. The rate used is the latest observation dated at least one day before the previous session, which allows for FRED's next-day posting. Monthly bill returns compound the daily accruals.
- **CAPM.** Ordinary least squares of strategy excess return on SPY excess return (both minus bills), on two frequencies:
  - monthly: calendar-month returns between month-end sessions, 40 months per in-sample trial, 33 for trial 4;
  - daily: 834 to 838 sessions.

  Alpha is annualised ×12 monthly and ×252 daily, with OLS t-statistics. Tracking error is the annualised standard deviation of monthly (strategy − SPY), and IR is that difference's annualised mean divided by TE. These TEs match the stored `tracking_error_spy` to within 0.1 pp for the month-end trials (1 to 4, 13, 14); the daily and weekly variants differ because the stored figure is per-period. The **split** uses the identity strategy − SPY = α + (β − 1)(SPY − bills) + ε. Its mean, annualised, is "from alpha" (12α) plus "from beta" ((β − 1) × the SPY excess return). The two columns sum to the arithmetic active return.
- **Synthetic hedged line (point in time).** For each month m:
  - β̂_m is the OLS beta of daily excess returns over the 126 sessions ending at the last session of month m − 1, so it uses no data from month m or later;
  - the hedged return is R_strategy,m − β̂_m × R_SPY,m. The SPY short is set at the month start. The short proceeds earn no rebate and the short pays SPY's dividends; there is no borrow fee (SPY is easy to borrow) and no extra trading cost on the hedge;
  - excess return = hedged − bills.

  The first month with 126 prior sessions is April 2021, so the hedged line covers 33 months in-sample and 26 for trial 4. A second version caps β̂ at 1 (the Reg T fit in the beyond-long-only report).
- **Script.** A scratch script in the team's scratchpad, not committed (read-only analysis; the owner's rule for diagnostics). The method above is complete enough to recompute every number.

## Evidence

**CAPM against SPY, 15 bp per side** (percentages are pp/yr; "from alpha" + "from beta" = mean active return against SPY):

| Trial | Book | CAGR | SPY CAGR | β monthly | β daily | R² monthly | α/yr monthly | t(α) monthly | α/yr daily | t(α) daily | TE | IR | From alpha | From beta |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | H1 in-sample, trial 1 (pre-fix universe) | +5.1 | +11.1 | 1.04 | 1.14 | 0.60 | −4.7 | −0.54 | −4.6 | −0.44 | 15.4 | −0.28 | −4.7 | +0.4 |
| 2 | H1 in-sample, trial 2 | +5.3 | +11.1 | 1.04 | 1.14 | 0.60 | −4.5 | −0.52 | −4.4 | −0.41 | 15.5 | −0.27 | −4.5 | +0.4 |
| 3 | B3 GP/A in-sample | +7.8 | +11.1 | 1.08 | 1.10 | 0.84 | −3.2 | −0.66 | −3.1 | −0.55 | 8.6 | −0.28 | −3.2 | +0.7 |
| 4 | H1 holdout (spent; stored equity only) | +24.8 | +20.3 | 1.92 | 1.63 | 0.61 | −6.6 | −0.55 | −1.8 | −0.14 | 21.3 | +0.34 | −6.6 | +13.8 |
| 11 | T114 v1: top 20%, daily | +4.9 | +11.1 | 0.96 | 1.02 | 0.69 | −4.8 | −0.74 | −4.8 | −0.60 | 11.6 | −0.45 | −4.8 | −0.4 |
| 12 | T114 v2: top 5%, daily | +4.8 | +11.1 | 1.10 | 1.17 | 0.45 | −4.2 | −0.34 | −3.8 | −0.28 | 22.2 | −0.14 | −4.2 | +1.0 |
| 13 | T114 v3: top 20%, month-end | +4.8 | +11.1 | 0.96 | 1.06 | 0.69 | −4.9 | −0.76 | −5.1 | −0.63 | 11.5 | −0.46 | −4.9 | −0.4 |
| 14 | T114 v5: top 5%, month-end | +8.5 | +11.1 | 1.18 | 1.20 | 0.50 | −1.5 | −0.12 | −0.6 | −0.04 | 21.4 | +0.02 | −1.5 | +1.8 |
| 15 | T114 v4: top 5%, week-end | +8.4 | +13.0 | 1.13 | 1.15 | 0.40 | −2.4 | −0.17 | −2.2 | −0.16 | 24.7 | −0.03 | −2.4 | +1.5 |
| 16 | T114 v6: top 20%, week-end | +6.6 | +13.0 | 0.97 | 1.02 | 0.62 | −4.8 | −0.63 | −5.0 | −0.62 | 13.3 | −0.39 | −4.8 | −0.4 |

Notes:
- Trials 15 and 16 start on 2020-09-04, the first week-end fill, so their SPY CAGR differs.
- SPY's excess return over bills was 9.9 pp/yr in-sample (11.6 for trials 15 and 16) and 15.0 pp/yr in the holdout. Bills compounded to 2.2%/yr in-sample and 4.5%/yr in the holdout.
- Daily betas sit above monthly ones for most books. The gap is significant above 1 on daily data for the top-5% books, B3 and H1 (t(β − 1) from 3.0 to 4.9), but not on monthly data (t from 0.3 to 1.0). Only the holdout's beta is clearly above 1 on both (t = 3.3 monthly, 12.0 daily).

**Synthetic hedged line** (trailing 126-session point-in-time β̂, short β̂ × SPY, no rebate; the hedge starts at the 2021-03-31 close, 2024-07-31 for trial 4):

| Trial | Months hedged | β̂ mean [min, max] | Hedged CAGR | Bills CAGR | Excess over bills, pp/yr | t | Vol | Sharpe vs bills | Excess, β̂ capped at 1 | Unhedged CAGR, same months | SPY CAGR, same months |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 33 | 1.27 [0.76, 2.12] | −11.0 | +2.6 | −13.2 | −1.54 | 14.2 | −0.93 | −10.5 | −1.5 | +8.4 |
| 2 | 33 | 1.27 [0.75, 2.11] | −10.5 | +2.6 | −12.7 | −1.51 | 14.0 | −0.91 | −10.1 | −1.1 | +8.4 |
| 3 | 33 | 1.08 [0.90, 1.22] | −5.5 | +2.6 | −7.9 | −1.61 | 8.3 | −0.97 | −7.7 | +2.1 | +8.4 |
| 4 | 26 | 1.77 [1.11, 2.60] | −3.8 | +4.2 | −5.8 | −0.42 | 20.3 | −0.29 | +4.8 | +23.5 | +17.6 |
| 11 | 33 | 1.13 [0.74, 1.75] | −10.8 | +2.6 | −13.3 | −2.01 | 11.1 | −1.21 | −11.1 | −1.9 | +8.4 |
| 12 | 33 | 1.30 [0.82, 2.13] | −14.6 | +2.6 | −16.5 | −1.47 | 18.7 | −0.89 | −13.4 | −5.0 | +8.4 |
| 13 | 33 | 1.17 [0.74, 1.82] | −9.6 | +2.6 | −12.1 | −1.88 | 10.7 | −1.13 | −10.0 | −0.8 | +8.4 |
| 14 | 33 | 1.34 [0.79, 2.23] | −9.7 | +2.6 | −11.1 | −1.02 | 18.0 | −0.62 | −8.2 | −0.2 | +8.4 |
| 15 | 33 | 1.28 [0.81, 2.12] | −12.3 | +2.6 | −14.0 | −1.26 | 18.4 | −0.76 | −11.6 | −3.3 | +8.4 |
| 16 | 33 | 1.14 [0.74, 1.78] | −10.3 | +2.6 | −12.8 | −1.95 | 10.9 | −1.18 | −10.6 | −1.4 | +8.4 |

**Reading the hedged line.**
- From April 2021 to December 2023, the in-sample books made about −5 to +2%/yr while SPY made +8.4%/yr. Their gap to SPY over those 33 months (CAGR gap about −6 to −13 pp/yr) was wider than over the full 40 (−3 to −6), so the first seven months, which no trailing β̂ can hedge, were the books' relatively strong stretch.
- Shorting β̂ × SPY removed SPY's gain. What was left was that negative alpha plus a further loss from over-hedging: β̂ averaged 1.1 to 1.3 against realised betas near 1.0.
- With the hedge capped (β̂ ≤ 1), the holdout book left up to 1.6 (about 0.8 on average) of its beta unhedged. That is the only version above bills (+4.8 pp/yr), and it is above bills because it still holds market exposure.

## Disconfirmation
- **What would overturn the "not market exposure" reading?** A beta low enough that (β − 1) × the market premium accounts for the shortfall. With a 9.9 pp/yr premium, H1's −4.1 pp/yr active return would need β ≈ 0.6. Every observed beta is 0.96 or higher (monthly) and 1.02 or higher (daily), so the beta term is between −0.4 and +1.8 pp/yr in-sample: market exposure added to the return or cost at most 0.4 pp/yr. Higher daily betas only strengthen this.
- **Is the alpha just noise?** Yes, possibly: every |t(α)| is below 1. "Momentum lost by picking" means the shortfall sits in the alpha term, not that a negative alpha has been proven. Neither a positive nor a negative alpha is established. With 10 rows scanned, the largest |t| (0.76) is not even nominally significant, so no deflation changes the conclusion.
- **Is the holdout split robust?** The beta term (+13.8 pp/yr) is large and its β > 1 is significant (t = 3.3 monthly). Daily data give a smaller negative alpha (−1.8 pp/yr), but the same sign and the same conclusion: the excess return over SPY is market exposure.

## Caveats & gaps
- **One market.** CAPM against SPY only. Equal-weighted books carry a size tilt that a cap-weighted index scores as alpha. Some of the negative in-sample alpha may be size, not momentum selection. This report does not split it out. B12's equal-weighted-universe control does.
- **Short window.** 40 in-sample months. Alpha standard errors are about 5 to 14 pp/yr, so this report can tell the sign of the split, not the size of the alpha.
- **The hedged line is a diagnostic, not a book.** It assumes:
  - no hedge trading cost;
  - no borrow fee;
  - no rebate, which is retail-realistic;
  - the hedge set monthly at the month start.

  It starts in April 2021 because of the 126-session window; a longer window would start later. The over-hedging comes partly from betas estimated in the volatile 2020-21 tape.
- **The T-bill proxy is outside the store.** DTB3 was fetched ad hoc with a one-day posting lag, not stored with `known_at`. Discount-basis accrual understates the bond-equivalent yield by a few basis points a year, which does not matter at this scale.
- **Trial 4 is spent.** These numbers describe a holdout already spent once; they are not a new test of H1 and do not count as a second look for a decision.
- **Trial 1** ran on the pre-fix universe and is shown for completeness; trial 2 supersedes it.

## UNVERIFIED items
- Every number in the tables was computed from the store's `trial_equity` rows and FRED DTB3 by the scratch script; none is unverified.
- "Equal-weight lagged cap-weight sharply in 2023" is general market knowledge, not computed here. Whether the trials' universe-level size tilt explains part of the alpha is open (follow-up below).

## Follow-up questions (not answered here)
- How much of the negative alpha is a size tilt? Regress on SPY and an equal-weighted universe return, which B12 builds anyway.
- Does B3's alpha change once B3 has a longer history (the fundamentals backfill to 2016, B12's M option)?

## Sources
- The owner's store `data/tradepartner.duckdb`, read-only on 2026-10-10: `trial_equity`, `trials`, `hypotheses`, `trial_metrics` (for the cross-check).
- FRED, DTB3 (3-Month Treasury Bill Secondary Market Rate, Discount Basis), `https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3`, fetched 2026-10-10.
- [Beyond long-only](2026-10-10-beyond-long-only.md): B11 row, step 0, LS-13 to LS-15.
