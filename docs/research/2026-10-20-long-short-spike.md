# Long/short spike: H1's momentum ranking as a 50/50 book is killed (T218)

**Date:** 2026-10-20  ·  **Issue:** #1876 (run result), #2013 (this report)  ·  **Status:** COMPLETE  ·  **Kind:** a registered exploratory `return` run, one run, two configurations  ·  **Experiment:** [long-short-spike](../experiments/long-short-spike.md) (backtest plan T218)

## Answer

**Verdict (the pre-declared kill rule, applied as written): the long/short `momentum` idea on H1's ranking is KILLED.** The primary, the net spread at 30 bp a year of borrow, has a point estimate of **-6.82 pp/yr** (95% one-sided-bound interval -24.91 to +11.27, 40 monthly periods, standard error 11.0). The rule kills the idea if the point estimate is below 0 or its upper bound is below +2.0 pp/yr. The point estimate is below 0, so it is killed. The upper bound (+11.27) is not below +2.0, so that branch did not fire; the point estimate alone decides.

**This gates nothing.** The experiment file, the plan's T218 line and the owner's decision of 2026-10-19 on #1876 say the same thing: the rule is kill-only, it kills only this one long/short `momentum` idea, and T219 onward still build. The owner builds the shorting capability whatever this run shows.

**What the result does not say.** The interval spans zero by a wide margin (half-width about 18 pp/yr). Forty months can kill by the registered rule; they cannot show that the true spread is negative. The run says that on this window the bottom decile did not underperform the top decile after costs, not that it never will.

## The run

| Item | Value |
|---|---|
| Registry | registration 1 (`long-short-spike`), dataset 1 (`long-short-spike-sessions`), **run 1**, outcome `ok`, verdict `n/a` (no threshold) |
| Run date | 2026-10-19, by the owner, on the real store |
| Artifact | `data/research/long-short-spike/long-short-spike-run1.json` (gitignored), sha256 `8a5d1e07...d1a4` as the orchestrator recorded it on #1876 |
| Window | 2020-08-31 to 2023-12-29, 41 month-end sessions, 40 monthly periods; the last period ends at close(2023-12-29) |
| Book | 0.5 x H1's long leg - 0.5 x the bottom-decile short leg (names with a raw close of at least $5), 15 bp per side on both legs, borrow b/12 a month on the short half, free cash on the store's DTB3 accrual |
| Holdout | untouched: the window ends before 2024-01-01 and the artifact's last read is at the close of 2023-12-29 |

Every number below is the recorded result (the orchestrator's comment on #1876, quoting the run's summary); none is recomputed here.

## Results

| Metric (recorded name) | Value |
|---|---|
| **Net spread at 30 bp** (`net_spread_b30_pp_per_year`, primary) | **-6.82 pp/yr** |
| 95% interval (mean -/+ 1.645 Newey-West standard errors, 3 lags) | -24.91 to +11.27 |
| n (monthly periods), standard error | 40, 11.0 |
| Gross spread (`gross_spread_pp_per_year`) | -4.02 pp/yr |
| Long leg, gross CAGR (`long_leg_cagr_gross`) | +6.8% a year |
| Short leg, gross CAGR (`short_leg_cagr_gross`) | +8.6% a year (the shorts beat the longs) |
| Book CAGR at 30 bp borrow (`book_cagr_b30`) | -3.0% a year |
| Book volatility at 30 bp (`book_vol_b30`) | 11.8% a year |

Reading it: a short leg that returns +8.6% a year gross costs the book that much, so the stock-picked short half lost money on this window. The bottom decile of a 12-1 momentum ranking, in a top-1,000 universe from late 2020 to the end of 2023, rose faster than the top decile. That is the size-tilt and junk-rally pattern the experiment file expected to check against the equal-weighted universe. The gross spread (-4.02) is already negative before any cost; trading and borrow take it to -6.82. The CAGRs are geometric, the spread is an arithmetic mean scaled to the 50/50 book, so the leg figures do not subtract to the spread.

The secondary values not quoted here (the b = 0 and b = 300 lines, the SPY-hedged control, the short leg against the equal-weighted universe, the worst short month, the drift-cover counts, the delisted-short count, the book minus hedged control) are in the artifact above. They were not copied into this report, and the B12 park-rule readout and the control comparison are therefore not stated here. They are for the record only; the kill rule does not read them.

## Red flags (the experiment file's list)

| Flag | Threshold | Recorded | Clear? |
|---|---|---|---|
| Gross spread | above 20 pp/yr | -4.02 | yes |
| `floor_replaced_share` | above 0.2 | 0.0 | yes |
| `spy_cagr` | far from 11.1% | 11.1% | yes |
| `rf_cagr` | outside about 1.5% to 2.5% | 2.21% | yes |
| `meta.rf_source` | must read `store` | `store` | yes |
| `meta.max_read_t` | not after close(2023-12-29) | 2023-12-29 21:00 UTC | yes (see below) |

The experiment file and the owner sheet said "not after 20:00 UTC". 21:00 UTC is the 16:00 close on 2023-12-29, which is in EST (UTC-5); 20:00 UTC assumed EDT. The read is at the close, not after it. A `floor_replaced_share` of exactly 0.0 means the $5 floor replaced no name in any month, which fits a top-1,000 universe whose own rule already removes sub-$5 names (ADR 0006). The remaining flags in the file (`max_short_weight`, `n_delisted_short`, the control against B11's hedged line) are secondary values in the artifact and are not reported here.

## The engine check

This is not part of the registered result. The signed engine rests on spec item 5: a short marked on the dividend-adjusted frame differs from paying the dividend in lieu from cash only by a second-order term. The script's `engine-check` subcommand tested it on the fixture store before the experiment file merged (2026-10-19, team shortspike). It reads no real data and opened no run.

- **Setup:** a hand two-name 50/50 book, short SEC_SPY across its $1.35 dividend of 2018-06-25 (prior close $239.52), long SEC_MTUM, half of equity on each leg, once marked on the adjusted frame and once paying the dividend from cash.
- **Not identical at the ex-date close.** The two books differ by -4.4 x 10^-6 of equity there, about 0.2% of the dividend paid.
- **The form of the difference.** Over the six sessions from the ex-date, it equals n.D.(1 - P(s) / (P(X-1) - D)) to 1.6 x 10^-16. Here n is the shares short, D the dividend, P(s) the raw close on session s and X the ex-date. This is the store's factor convention (1 - D / prior close): the adjusted mark re-shorts the dividend at the prior close less the dividend, so the second-order term is non-zero already at the ex-date close.
- **Consequence.** The term is as small as the spec says. Spec item 5 and the T218, T220 and T222 plan lines state this prior-close form (owner, 2026-10-19, #1876); T220 keeps the check as a pin. The run's own dividends sit inside the adjusted marks, as the engine's will, so the check changes nothing in this run.

I did not re-run the check for this report; the figures are the experiment file's, as recorded before registration.

## What this closes, and what it does not

- **Closed:** no long/short `momentum` hypothesis file is written on H1's ranking. The idea is retired under its registration; no variant runs under this slug. A later long/short `momentum` hypothesis discloses this run in its preamble (the experiment file's retirement condition). The run counts in `momentum`'s N (research-registry req 9).
- **Not closed:** the signed-engine build (T219 to T224) and the paper plan's gated side are unaffected. Other long/short ideas (other rankings, composites with profitability, a different floor or fee) are not tested by this run and are not killed by it. B12 remains the full decomposition if it is run, and its park rule is not evaluated here.
- **Consistent with the priors.** LS-3 (NOT SUPPORTED: in large caps, single-stock shorts add no net value over an index hedge of the long leg) and the beyond-long-only report's prior of about -2 pp/yr (range -7 to +3) against T-bills for a dollar-neutral momentum composite. The recorded net spread, -6.82 pp/yr, is at the bottom of that range, with an interval that contains all of it. This is one in-sample window, and the long leg had already been seen (H1's trials 1 and 2).

## Caveats

- One run, one design, 40 monthly periods, one window (2020-08-31 to 2023-12-29), dominated by a mega-cap-led market and the 2022 to 2023 rate move. The upper bound of +11.27 pp/yr means a positive spread is not excluded.
- Exploratory (`confirmatory = false`), no multiplicity method, no DSR, no selection; one pre-declared design.
- A dollar-neutral reading is not the same as a hedged one: the book's market beta is not reported here.
- Borrow is a flat declared fee (0, 30, 300 bp a year), not a quoted locate cost; the $5 floor is the shortability proxy of the backtest spec's amendment of 2026-10-19, not a real locate list.

## Sources

- [Experiment file](../experiments/long-short-spike.md) (pre-declared design, kill rule, red flags, engine check); the orchestrator's result comment on #1876 and the owner's decisions on that issue (2026-10-19).
- [Backtest plan](../plans/backtest.md) T218; [backtest spec](../specs/backtest.md) amendment of 2026-10-19; [ADR 0024](../decisions/0024-lift-long-only-and-top-1000.md).
- [Beyond long-only](2026-10-10-beyond-long-only.md) (LS-3, the B12 prior); [B11 beta-alpha split](2026-10-10-b11-beta-alpha-split.md); claim TP-7 in [claims.toml](claims.toml).
