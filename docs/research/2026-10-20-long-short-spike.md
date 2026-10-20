# Long/short spike: H1's momentum ranking as a 50/50 book is killed (T218)

**Date:** 2026-10-20  ·  **Issue:** #1876 (run result), #2013 (the report), #2018 (the secondary values and the three red flags)  ·  **Status:** complete (the kill verdict, the primary and leg figures, the artifact's secondary values and the three red flags are all recorded; the `n_delisted_short` listing ends sit outside the artifact and are not read here, see Open items)  ·  **Kind:** a registered exploratory `return` run, one run, two configurations  ·  **Experiment:** [long-short-spike](../experiments/long-short-spike.md) (backtest plan T218)

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
| Book CAGR at 0 bp and 300 bp borrow (`book_cagr_b0`, `book_cagr_b300`) | -2.82% and -4.27% a year |
| Book excess over T-bills, 0 / 30 / 300 bp (`book_excess_rf_b0`, `_b30`, `_b300`) | -4.35% / -4.50% / -5.86% a year |
| SPY-hedged control CAGR, 0 / 30 / 300 bp (`hedged_cagr_b0`, `_b30`, `_b300`) | -1.24% / -1.39% / -2.71% a year |
| SPY-hedged control excess over T-bills, 0 / 30 / 300 bp (`hedged_excess_rf_b0`, `_b30`, `_b300`) | -3.11% / -3.26% / -4.61% a year |
| SPY-hedged control volatility at 30 bp (`hedged_vol_b30`) | 8.13% a year |
| Book minus SPY-hedged control, 0 / 30 / 300 bp (`book_minus_hedged_cagr_b0`, `_b30`, `_b300`) | -1.58% / -1.58% / -1.56% a year |
| Equal-weighted universe CAGR (`ew_universe_cagr`) | +10.35% a year |
| Short leg minus equal-weighted universe (`short_minus_ew_universe_cagr`) | -1.75% a year |
| Worst short month (`short_leg_worst_month`; the period rebalanced 2020-10-30, read 2020-12-01) | +26.2% |
| `max_short_weight` (largest single short weight; fill session 2023-01-03) | 1.12% |
| `months_short_weight_above_cover` | 0 |
| `n_delisted_short` | 11 |

Reading it: on this window the bottom-decile short leg returned more (+8.6% a year gross) than the top-decile long leg (+6.8%), so the short half lost money and the gross spread (-4.02) is already negative before any cost. Trading and borrow take it to -6.82. The spread is the full long-minus-short figure (2 x (book - 0.5 x T-bill), before cash interest); the leg CAGRs are geometric and the book holds each leg at half of equity, so the leg figures do not subtract to the spread. The short leg rose because the whole universe rose: the equal-weighted universe gained +10.35% a year and the bottom decile trailed it by 1.75 pp/yr, so the shorts did underperform the average name, but the universe rose, so a short of the average name still lost. The long leg (the top decile) rose less than the universe too.

The artifact's other secondary values are recorded in the table above. The b = 0, 30 and 300 lines run -2.82%, -2.97% and -4.27% a year, and the book sits 4.35, 4.50 and 5.86 pp/yr below T-bills. The SPY-hedged control (0.5 x the same long leg - 0.5 x SPY) lost 1.24%, 1.39% and 2.71% a year, better than the stock book at every borrow line, and sits 3.11, 3.26 and 4.61 pp/yr below T-bills. The book is 1.58, 1.58 and 1.56 pp/yr worse than the hedged control, so swapping the stock short leg for a 0.5 x SPY hedge would have added about 1.6 pp/yr. The kill rule reads none of these.

## Red flags (the experiment file's list)

| Flag | Threshold | Recorded | Clear? |
|---|---|---|---|
| Gross spread | above 20 pp/yr | -4.02 | yes |
| `floor_replaced_share` | above 0.2 | 0.0 | yes |
| `spy_cagr` | far from 11.1% | 11.1% | yes |
| `rf_cagr` | outside about 1.5% to 2.5% | 2.21% | yes |
| `meta.rf_source` | must read `store` | `store` | yes |
| `meta.max_read_t` | not after close(2023-12-29) | 2023-12-29 21:00 UTC | yes (see below) |
| `max_short_weight` | above 0.10 in many months | 1.12% peak, 0 months above | yes |
| `n_delisted_short` | above 10 | 11 | no (count above 10, no large gain — see below) |
| Control vs B11's hedged line | a different sign of the excess over T-bills | below T-bills at every borrow line | yes (same sign — see below) |

The experiment file and the owner sheet said "not after 20:00 UTC". 21:00 UTC is the 16:00 close on 2023-12-29, which is in EST (UTC-5); 20:00 UTC assumed EDT. The read is at the close, not after it. A `floor_replaced_share` of exactly 0.0 means the $5 floor replaced no name in any month, which fits a top-1,000 universe whose own rule already removes sub-$5 names (ADR 0006). The three flags the first report left unread are read here. `max_short_weight` peaks at 1.12% (2023-01-03), barely above the 1/101 equal weight, and `months_short_weight_above_cover` is 0, so no month came near the 0.10 drift-cover trigger in spec item 7 and no single name jumped by an implausible factor. `n_delisted_short` is 11, one above the experiment file's guide of 10, so the count flag is set; the listing ends are not in the artifact and this report does not read the store, so the count is read but not fully retired. The file's second condition — a delisted short with a large gain at its last close — does not fire: the largest last-close mark among the 11 is +19.6% (`0001594466`, the January 2021 period; a loss for the short) and the rest run from -3.7% to +13.7%, so no mark is an implausible jump. A bound from the artifact's own weights: each delisted short is 1 of 101 short names for one month, so it moves that month's net spread by 1/101 of its return; even if all 11 actually fell to zero at delisting (the true return's floor, -100%) while the artifact recorded the positive marks above, the spread would rise by at most (1/101) x (the sum of the 11 marks) = 11.61/101 = 0.115, about 3.45 pp/yr, leaving the net spread near -3.4 pp/yr, still below 0. So the listing ends' worst case does not reach the kill line. The control's excess over T-bills (-3.26 pp/yr at 30 bp; -3.11 and -4.61 at 0 and 300) has the same sign as B11's hedged line (8 to 17 pp/yr below T-bills, 8 to 13 with the beta capped at 1), so the flag — a different sign — does not fire; the gap to B11's range (5 to 14 pp/yr less below T-bills) is wider than the "few pp/yr" the experiment file expects, but the sign is the same, and B11 hedged by trailing beta over April 2021 to December 2023 while this run hedges at 0.5 x SPY over 2020-08-31 to 2023-12-29. The three flags exist to catch a broken run; none of them shows one, so the verdict stands.

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
- **The comparison with the priors is now made from the recorded values.** LS-3 (single-stock shorts versus an index hedge of the long leg) is not supported on this window: the book minus the SPY-hedged control (`book_minus_hedged_cagr_b30`) is -1.58 pp/yr, so the stock short leg subtracted from an index hedge. The beyond-long-only prior for a dollar-neutral momentum composite (about -2 pp/yr, range -7 to +3) is the book's excess over T-bills; the recorded `book_excess_rf_b30` is -4.50 pp/yr, below the prior's point estimate and inside its range. The recorded `spy_cagr` (11.1%) is above the short leg's gross 8.6%. The long leg had already been seen (H1's trials 1 and 2).

## Open items

- The artifact's secondary values and the three red flags are now recorded (this follow-up, #2018); none shows a broken run, so the verdict stands. One item is only partly retired: `n_delisted_short` is 11, above the experiment file's guide of 10, and clearing that count in full needs each delisted short's listing end in the store, which this report does not read; the artifact's last-close marks show no large short gain (largest +19.6%), and the worst case for the unread ends is bounded at about 3.45 pp/yr of spread, which does not reach the kill line.

## Caveats

- One run, one design, 40 monthly periods, one window (2020-08-31 to 2023-12-29), dominated by a mega-cap-led market and the 2022 to 2023 rate move. The upper bound of +11.27 pp/yr means a positive spread is not excluded.
- Exploratory (`confirmatory = false`), no multiplicity method, no DSR, no selection; one pre-declared design.
- A dollar-neutral reading is not the same as a hedged one: the book's market beta is not reported here.
- Borrow is a flat declared fee (0, 30, 300 bp a year), not a quoted locate cost; the $5 floor is the shortability proxy of the backtest spec's amendment of 2026-10-19, not a real locate list.

## Sources

- [Experiment file](../experiments/long-short-spike.md) (pre-declared design, kill rule, red flags, engine check); the orchestrator's result comment on #1876 and the owner's decisions on that issue (2026-10-19).
- [Backtest plan](../plans/backtest.md) T218; [backtest spec](../specs/backtest.md) amendment of 2026-10-19; [ADR 0024](../decisions/0024-lift-long-only-and-top-1000.md).
- [Beyond long-only](2026-10-10-beyond-long-only.md) (LS-3, the B12 prior); [B11 beta-alpha split](2026-10-10-b11-beta-alpha-split.md); claim TP-7 in [claims.toml](claims.toml).
