# Research Report: Which strategy classes earn a return that is not market beta, judged against T-bills (satellite objective)

**Brief:** #3696  ·  **Date:** 2026-11-21  ·  **Status:** INCOMPLETE. Page fetches were blocked for every primary host tried (docs.alpaca.markets, alpaca.markets, aqr.com, arxiv.org, ideas.repec.org and one PDF mirror), so every external figure below was read from search-engine summaries of the source, not from the source itself, and is marked UNVERIFIED. The grades are therefore provisional. The Alpaca facts that matter most are the repo's own recorded paper probe (2026-10-19), which is verified.  ·  **Agent/model:** team uncorr. Web search only, about 27 queries; no data pulled, no backtest, no order

## Answer
**Verdict:** MIXED at best. No class has SUPPORTED post-2015 evidence of an **edge** (a return nobody is paid to bear risk for) that this account can reach. Two classes show a small **risk premium** above T-bills with near-zero equity beta: managed-futures trend and merger arbitrage. Index option selling is mostly **beta in disguise**: the evidence that anything is left after its equity beta has weakened since about 2010.  ·  **Confidence:** low to medium. The direction is consistent across sources. Every magnitude is UNVERIFIED (see Status).

In plain words:

1. **The new yardstick is fair, but it does not create returns.** Judged against T-bills with beta taken out, most of what looked like "income" disappears. A covered call or put-write keeps roughly 0.5 to 0.65 of the market's beta. The owner already holds that beta in his core.
2. **Index option selling (BXM, PUT): its non-beta part is about zero since about 2010.** Over the long run, PUT matched the S&P 500's return at about two-thirds of the volatility: 9.40% against 9.91% a year through August 2023, with volatility of 10.26% against 15.38%. Since 2007 it returned 6.7% a year against 10.7%, with a maximum drawdown of −32.7% against −50.9%. With a beta of 0.5 to 0.6, that leaves an alpha of about −0.2 to +0.7 pp a year (our arithmetic). A 2025 Chicago Fed working paper (Dew-Becker and Giglio) reports that over the past 15 years "option alphas have become indistinguishable from zero". The premium was real in the older data; it is not shown to be there now. Label: **beta in disguise**, plus a small and uncertain short-volatility premium.
3. **0DTE options and "the wheel" are not supported.** Retail 0DTE buyers lose money, and market makers take the fees (Beckmeyer, Branger and Gayda 2023). For the sellers' side, the only academic source found (2020 to 2024) says transaction costs and estimation risk leave Sharpe ratios that are "economically insignificant". The wheel is a covered call on one stock: equity beta plus single-name risk. No independent evidence of a net premium was found.
4. **Managed-futures trend has the best case as a diversifier, and it is a premium, not an edge.** Its correlation with the S&P 500 is near zero (−0.10 to +0.38, depending on the fund and window). It earned large gains when stocks fell in 2022 (DBMF +21.5%, KMLM +30.4%). It also had long dry spells: the Barclay CTA index's Sharpe ratio was about 0.1 ± 0.3 from 2009 to 2019, and the SG Trend Index lost 18.7% in the 12 months to April 2025, the worst in its 25 years. Over 2022 to 2025, DBMF compounded about 7.8% a year and KMLM about 4.1% (our arithmetic from annual figures), against T-bills of about 4%. Label: **risk premium**, behavioural or crisis-insurance in origin. Alpaca can buy these ETFs today.
5. **Merger arbitrage survives in fund form as a thin premium.** The Merger Fund (MERFX): 3.9% a year over 10 years at 3.8% volatility. The MNA ETF: about 2.0% a year, beta 0.08 to 0.21, maximum drawdown −16.7% (March 2020). T-bills averaged about 2.2% a year over 2016 to 2025. So the excess is about +1.7 pp a year for the active fund and about zero for the index ETF. Spreads shrank by over 400 bp after 2002 (Jetley and Ji 2010). The return is "selling puts on deals": its losses arrive in crashes. Label: **risk premium**. The do-it-yourself book (IC-B3) stays blocked on data and cost, and Alpaca cannot buy mutual funds such as MERFX (UNVERIFIED).
6. **Market-neutral equity: the one strong live record is closed, and the retail ETF loses money.** AQR's QMNIX returned 7.54% a year against 2.04% for T-bills from 2014 (a prior report). Along the way it lost −11.7%, −11.3% and −19.6% in 2018 to 2020 (maximum drawdown −38.8%, November 2020), then made 22.7% a year over the five years to 2025. It closed to new investors on 2026-06-19. BTAL (anti-beta) has a beta of −0.47 to −0.65 and lost about 1 to 5% a year: it is insurance, not alpha. Label: QMNIX is part **edge**, part premium, and not reachable. BTAL is **negative beta**.
7. **Everything else is weaker.**
   - Long Treasuries: the term premium was negative for much of 2016 onward, and TLT fell −48% from August 2020 to October 2023.
   - Short VIX ETPs: XIV lost 96.3% on 2018-02-05 and was terminated.
   - Pairs trading: unprofitable net after 2002 (LS-10).
   - The event long/short books from this week's nearest misses: a hedge adds no alpha, and Alpaca pays no rebate on short proceeds (LS-13, LS-17).
   - Closed-end fund discounts: no post-2010 net-of-cost study found.
8. **What Alpaca can do with options today.**
   - **Paper (verified in the repo):** level 3 by default; multi-leg (`mleg`) orders fill as a unit; SPY options are American style with a 100-share multiplier and integer quantities only.
   - **Live (UNVERIFIED):** level 1 is covered calls and cash-secured puts; level 2 adds long options; level 3 (spreads and other multi-leg orders) needs an application. Commissions are $0, with regulatory and exchange fees on top.
   - **Index options (UNVERIFIED):** SPX, SPXW and XSP (European, cash-settled, eligible for §1256 tax treatment) are live through the API. Naked index shorts, calendars and diagonals are not supported at launch, so a cash-secured XSP put-write is not available; defined-risk spreads probably are.
   - **Size:** one XSP or SPY contract controls about $60,000 or more, so $100 can run nothing and $10,000 can run one or two defined-risk spreads or one cash-secured put on a stock priced at $100 or less.
   - **Charter:** it says "no options trading" and "no leverage", so any options test needs an ADR first.
9. **Direct answers.** In brief (details under "Direct answers"):
   - (i) If one class gets a registered test, the evidence ranks **managed-futures trend via ETFs** first.
   - (ii) At $10,000, a realistic **non-beta** gain is about **$0 to $300 a year above T-bills**, with a worst year of about **−$1,900** (trend) or −$1,700 to −$2,100 (merger arbitrage).
   - (iii) The satellite objective opens a door to **premia, not edges**. For this owner, the evidence does not show that any class beats "T-bill ladder plus the market" by enough to measure within the project's life.

### Per-class table
All figures are UNVERIFIED (read from search summaries) unless marked. "Alpha after beta" is the return above T-bills after subtracting beta × the equity premium. "n/f" means not found.

| # | Class | Net return (period) | Vol | Max DD | Beta to SPY | Alpha after beta vs T-bills | Grade (post-2015, non-beta, net) | Edge / premium / beta | Alpaca today | Min sensible account |
|---|---|---|---|---|---|---|---|---|---|---|
| c | Managed-futures trend ETFs (DBMF, KMLM) | DBMF ~7.8%/yr, KMLM ~4.1%/yr (2022-25, own arithmetic); SG Trend −18.7% in 12 mo to Apr 2025 | n/f (~8-12% typical, unverified) | −18.7% (index, 12 mo) | ~0 (corr −0.10 to +0.38) | about 0 to +4 pp (2022-25); 2009-19 Sharpe ~0.1 (Barclay CTA) | **MIXED** | risk premium | yes (ETFs; long only) | $100 (fractional ETF) |
| b | Merger arbitrage (MERFX / MNA; DIY book) | MERFX 3.9%/yr (10y); MNA ~2.0%/yr (10y) | MERFX 3.8% | MERFX −20.8% (period unclear); MNA −16.7% (Mar 2020) | 0.08-0.21 (MNA) | MERFX ~+1.7 pp; MNA ~0 | **MIXED** (fund); DIY **INSUFFICIENT** | risk premium (put-like) | MNA/MRGR ETFs yes; MERFX no (unverified); DIY long-only yes mechanically, data blocked | ETF $100; DIY ~$10k for 5-10 deals |
| d | Market-neutral multi-factor (QMNIX) | 7.54%/yr vs T-bills 2.04% (2014 to 2026-09) | n/f | −38.8% (Nov 2020) | ~0 | ~+5 pp (incl. post-boundary years) | **MIXED** (institutional, closed) | part edge, part premium | no (closed to new investors 2026-06-19) | n/a |
| d | Anti-beta (BTAL) | ~−1%/yr since 2011; −3.2 to −4.8%/yr (10y) | n/f | n/f | −0.47 to −0.65 | negative after beta adjustment is unclear; raw return below T-bills | **NOT SUPPORTED** as a positive return | negative beta (insurance) | yes | $100 |
| a | Index put-write / buy-write (PUT, BXM, BXMD, WPUT) | PUT 6.7% vs SPX TR 10.7% (2007 on); BXM trailed by 8.95 pp in 2025 | PUT 10.26% vs 15.38% (long run) | PUT −32.7% (since 2007) | ~0.5-0.65 | about −0.2 to +0.7 pp (own arithmetic, assumed beta) | **MIXED leaning NOT SUPPORTED** | beta in disguise + small short-vol premium | equity/ETF CSP and CC (L1); XSP spreads probably (L3); XSP cash-secured put no | ~$60k+ for one SPY/XSP CSP; ~$10k for one CSP on a ≤$100 stock |
| a | Collars | n/f | n/f | n/f | <0.5 | n/f | **INSUFFICIENT** | mostly reduced beta | equity/ETF yes (L1 call + L2 put, or L3) | ~$60k on SPY |
| a | Short strangles / iron condors (index/ETF) | n/f post-2015 | n/f | crash-dominated | ~0 at entry, short gamma | n/f | **INSUFFICIENT** | risk premium (short vol), ruin tail if naked | condors on paper yes; live L3 by application | ~$500 risk per XSP 5-pt spread; diversification needs far more |
| a | 0DTE selling | sellers' net Sharpe "economically insignificant" (2020-24) | n/f | n/f | ~0 | ~0 | **NOT SUPPORTED** | not an edge for retail | possible on paper (index options) | n/a |
| a | Wheel / single-stock earnings vol selling | none found | n/f | single-stock tail | ~0.5-0.7 × stock beta | n/f | **INSUFFICIENT** | beta in disguise + idiosyncratic risk | yes (L1) | ~$10k per contract on a $100 stock |
| f | T-bill ladder | = T-bill rate (~2.2%/yr avg 2016-25, own arithmetic) | ~0.3% | ~0 | 0 | 0 by definition | baseline (fact) | none (the benchmark) | yes (SGOV/BIL ETFs; Treasury purchase not via Alpaca, unverified) | $100 |
| f | Term premium (long Treasuries) | TLT −48% Aug 2020 to Oct 2023 | ~13-15% (unverified) | −48% | varies (negative to positive) | negative over 2016-2023 | **NOT SUPPORTED** post-2015 | risk premium (unstable) | yes (ETFs) | $100 |
| f | Credit carry | not searched | | | | | **INSUFFICIENT** (this report) | likely beta in disguise | yes (ETFs) | $100 |
| g | Closed-end fund discount book | pre-2010 studies only | | | ~1 unhedged (equity CEFs) | n/f | **INSUFFICIENT** | possible edge, unverified | long yes; NAV hedge needs shorts ($2k margin) | ~$10k+ |
| h | Short VIX ETPs | XIV −96.3% in one day (2018-02-05) | extreme | ~−96% | positive in crashes | n/a | **NOT SUPPORTED** (ruin) | risk premium with ruin tail | ETPs yes | none sensible |
| h | Long-vol hedges | negative carry (not quantified here) | | | negative | negative | **NOT SUPPORTED** as a return | insurance (pays a premium) | yes | n/a |
| i | Daily pairs / stat arb | net ~0 or negative post-2002 (LS-10) | | | ~0 | negative after no-rebate drag | **NOT SUPPORTED** | (was an edge, decayed) | needs margin ≥$2k + shorts | $30k+ |
| e | Event L/S from this week's nearest misses | below needed size vs SPY (PR #3693, #3691) | | | ~0 if hedged | ≈ alpha + (1−β)·rf − costs − rf, below 0 (LS-13, LS-17) | **NOT SUPPORTED** | edge if real, too small | shorts need ≥$2k margin | ≥$2k |
| j | SPX/XSP box spread (cash management) | ≈ T-bill rate, §1256 | ~0 | ~0 | 0 | ~0 | fact (financing rate) | none | XSP 4-leg: UNVERIFIED | ~$1k notional per XSP 10-pt box (unverified) |

### Ranking
**First**, by the strength of post-2015 net evidence of a return that is not beta:
1. Managed-futures trend ETFs: **risk premium**.
2. Merger arbitrage, active-fund form: **risk premium**.
3. Equity market-neutral multi-factor (QMNIX): **edge plus premium**, but not reachable.
4. Index put-write and covered calls: **beta in disguise**.
5. Everything else: NOT SUPPORTED or INSUFFICIENT.

**Then**, by what a $10,000 Alpaca account could actually run:
1. Trend ETFs: buyable today, fractional.
2. Merger-arbitrage ETFs: buyable, but their record is about the T-bill rate.
3. Covered calls and cash-secured puts on one cheap underlying: undiversified.
4. XSP defined-risk spreads: live support UNVERIFIED.
5. A do-it-yourself merger-arbitrage book: blocked on data.

The top two keep their order under both rankings.

### Direct answers
**(i) Is any class worth a registered test under the satellite objective, and which first?** Only as a test of a *premium*, not of an edge. The class with the most post-2015 evidence of a non-beta return that this account can reach today is **managed-futures trend held through an ETF (DBMF-type replicator)**. It is near-zero beta, positive over T-bills in 2022 to 2025, and buyable at Alpaca. A homemade ETF trend book is weaker than the ETF: it is long/flat with no futures, so it keeps equity beta in up-trends (PR #3682). This ranks the evidence; whether to register a test is the owner's call. A forward paper test cannot confirm it in any useful time: at an excess Sharpe of 0.3 to 0.5, t ≈ 2 takes 16 to 44 years (HO-11). A test could only catch a broken implementation or a gross failure.

**(ii) What would it earn at $10,000?** Our arithmetic; every input is UNVERIFIED.
- **T-bill ladder:** at an assumed 3.5 to 4% (today's rate not verified), about **$350 to $400 a year**, and about $0 in the worst year (2021).
- **Trend ETF:** T-bills plus about 0 to 3 pp, so about **$350 to $700 a year** in total, of which **$0 to $300 is non-beta excess**. Worst 12 months about **−$1,900** (SG Trend's −18.7%, 2024-25).
- **Merger-arbitrage fund:** T-bills plus about 0 to 1.7 pp, so about **$0 to $170 a year of excess**. Worst case **−$1,700 to −$2,100** (MNA −16.7% in March 2020; MERFX −20.8%, period unclear).
- **Put-write:** non-beta excess about **$0**. Worst case about **−$3,300** (PUT −32.7%), most of it the beta the owner already holds.

**(iii) Does the satellite objective open a door the SPY objective closed?** Partly. It lets trend and merger arbitrage be graded fairly: against SPY they looked like failures by construction, and against T-bills they are small positive premia with low correlation. But the evidence does not show an edge. The premia are about 0 to 3 pp a year, about $0 to $300 at $10,000, with drawdowns of 15 to 20% that come at awkward times. None is provable within the project's life. On expected value, the evidence says **a T-bill ladder plus the market is about as good as any of these for this owner**. The one honest argument for a trend sleeve is diversification in an equity crash, as in 2022, not extra return.

## Evidence
All sources accessed 2026-11-21. "Snippet" means read through a search-engine summary, not the page: UNVERIFIED by protocol.

| Claim | Source | Tier | Key figure (quoted or summarised) | OOS / post-pub? | Net of costs? |
|---|---|---|---|---|---|
| PUT matched the S&P over the long run with lower vol | Cboe benchmark-indices post [S1] (snippet) | 2 (exchange, interested party) | "through August 2023 … PUT 9.40% vs 9.91% … volatility 10.26% vs 15.38%" | long-run, includes post-2010 | index, no trading costs |
| PUT since 2007 trailed with a smaller drawdown | Cboe PUT factsheet [S2] (snippet; as-of date unclear) | 1 (index provider) | PUT 6.7% vs SPX TR 10.7% a year; max DD −32.7% vs −50.9% | yes (2007 on) | index, no costs |
| Covered calls: equity premium + vol premium + an unpaid reversal exposure; call writing cuts beta 1.00 → 0.64 | Israelov and Nielsen, FAJ 2014 and 2015 [S3] (abstract/snippet) | 1 (AQR authors, commercial interest disclosed) | short-vol component "Sharpe ratio near 1.0" but ~10% of risk; equity exposure most of risk and return; reversal ~25% of risk, little return | no (historical to ~2014) | gross |
| Option alphas ~0 over the past 15 years | Dew-Becker and Giglio, Chicago Fed WP 2025-17 [S4] (abstract via snippet) | 1 | "over the past 15 years, option alphas have become indistinguishable from zero"; traded options converged to synthetic | yes | n/f |
| VRP widened in 2023 despite option-income ETF growth | Cboe, "Are option income funds suppressing volatility?" [S5] (snippet) | 2 (interested party) | S&P 1M VRP 1.5% (2022) → 3.6% (2023) | yes | n/a |
| Option-ETF demand moves the implied-vol surface | AFA working paper [S6] (snippet) | 1 (WP) | flows "significantly affect" implied vol | yes | n/a |
| BXM lagged in 2025; derivative-income ETF inflows | First Trust [S7] (snippet) | 2 | S&P +17.86%, BXM 8.95 pp behind; category +$33B (2024), +$56B (2025) | yes | index |
| BXM beat the S&P in 4 of 17 years (2003-19) | First Trust [S7] (snippet) | 2 | as quoted | partly | index |
| Retail 0DTE buyers lose; market makers profit | Beckmeyer, Branger and Gayda 2023 (SSRN) [S8] (snippet) | 1 (WP) | ~75% of retail S&P option trades are 0DTE; average daily loss $184k → $358k after daily expiries (CXO summary) | yes | yes (incl. spreads) |
| Short-0DTE vol strategies not significant net | HKUST summary of a 2020-24 study [S9] (snippet) | 2 (summary of T1) | "transaction costs and estimation risk render the Sharpe ratios … economically insignificant" | yes | yes |
| Trend: 2009-19 weak | Babbedge and Kerson (Gresham) 2019 [S10] (snippet) | 2 | Barclay CTA Sharpe "0.1 +/- 0.3 s.e." | yes | net of fees |
| Trend: fewer large moves explain the 2010s | AQR "You can't always trend when you want" [S11] (snippet) | 2 | as summarised | yes | net |
| Short-term trend stopped working ~2009 | Kurth, Eisler, Rej and Bouchaud, "Is Trend Still Your Friend?" arXiv 2607.01550 [S12] (snippet) | 1 (preprint) | break around 2009 for short-term trends | yes | n/f |
| SG Trend worst 12 months | Virtus AlphaSimplex via advisor press [S13] (snippet) | 2 | −18.7% in 12 months to 2025-04-30, "worst … in the Index's 25-year history" | yes | net of fees |
| DBMF / KMLM annual returns and correlation | ETF Replay, PortfoliosLab [S14] (snippet) | 3 (data aggregators; figures only) | DBMF +21.5, −8.9, +7.3, +13.8 (2022-25); KMLM +30.4, −5.7, −1.7, −3.0; correlation −0.10 to +0.38 | yes | net of fees |
| Merger arbitrage earned ~4%/yr excess, put-like | Mitchell and Pulvino, JF 2001 (via IC-B3 review) | 1 | 4%/yr excess, 1963-1998 | no | yes |
| Spread shrank after 2002 | Jetley and Ji, FAJ 2010 [S15] (snippet) | 1 | "declined by more than 400 bps since 2002"; resumed decline after 2008-09 (Analysis Group update) | yes | n/a |
| MERFX and MNA records | PortfoliosLab, Kiplinger, Yahoo [S16] (snippet) | 3 (figures only) | MERFX 10y 3.90%, vol 3.76%, max DD −20.82%; MNA 10y ~1.96 to 2.3%, beta 0.08 to 0.21, max DD −16.68% (2020-03-18) | yes | net of fees |
| QMNIX record | AQR fund page (via prior report [LS]) and PortfoliosLab [S17] (snippet) | 2 / 3 | 7.54% vs 2.04% T-bills since 2014; 2018 −11.73, 2019 −11.30, 2020 −19.62; max DD −38.80% (2020-11-12); 5y to 2025 ~22.7%/yr | yes | net of fees |
| BTAL record | PortfoliosLab, AOL/Motley [S18] (snippet) | 3 | ~−1%/yr since 2011; beta −0.47 to −0.65; 2022 ~+20%, 2023 ~−15%, 2025 ~−20 to −23% | yes | net |
| XIV termination | Nasdaq/ETF.com, BIS QR March 2018 [S19] (snippet) | 1 (BIS) / 2 | XIV "lost 96.3% … on Monday"; acceleration at ≤20% of the prior indicative value; SVXY −83% in a day | yes | n/a |
| Negative term premium; TLT drawdown | NY Fed Liberty Street; PortfoliosLab [S20] (snippet) | 1 / 3 | ACM 10y term premium negative for much of 2016 on; TLT −48.35% from 2020-08-05 to 2023-10-19 | yes | n/a |
| CEF discount returns | Flynn 2005; Bradley, Brav, Goldstein and Jiang; ICI critique [S21] (snippet) | 1 (older) / 2 (advocacy) | excess returns for high-discount funds, gross; activism effect beyond mean reversion (1988-2003) | no post-2010 | no |
| Alpaca paper options behaviour | repo probe [2026-10-19-alpaca-sandbox-probe](2026-10-19-alpaca-sandbox-probe.md) | recorded (verified) | options level 3 on a new paper account; 2-leg `mleg` vertical filled as a unit; SPY contracts `style` american, `size` 100; integer qty only; no extended hours or IOC | n/a | n/a |
| Alpaca live options levels, fees, collateral | Alpaca docs and support [S22] (snippet; host blocked) | 1 (broker docs) | L1 "sell a covered call and sell cash-secured put"; Level 3 "All paper trading accounts will automatically have access … apply if you plan on live trading"; no commissions; CSP collateral = strike × 100 − premium | n/a | n/a |
| Alpaca index options | Alpaca blog and index-options doc [S23] (snippet) | 1 / 2 | SPX, SPXW, VIX, VIXW, DJX, XSP live via API; European, cash-settled; naked index shorts, calendars and diagonals not supported at launch; launch date conflicts (2026-02-09 vs September 2026) | n/a | n/a |
| Alpaca assignment and paper | Alpaca docs and forum [S24] (snippet) | 1 / 3 | paper NTAs visible "only the next day"; no exercise/assignment that would create a short stock position; auto-liquidation of at-risk positions near expiry; forum: XSP paper settlement bug "confirmed" by staff, fix date unknown | n/a | n/a |
| §1256 and straddles | 26 USC 1256 and 1092 (Cornell LII) and Pub. 550 [S25] (snippet) | 1 | broad-based cash-settled index options are nonequity options: 60% long-term / 40% short-term, marked to market at year end; qualified covered calls are exempt from the straddle rules | n/a | n/a |
| Option data prices | ThetaData; Cboe DataShop; ORATS; Intrinio [S26] (snippet) | 3 (vendor pages, tooling only) | ThetaData $40 / $80 / $160 a month (about 4 / 8 / 12 years of history); DataShop trades $100 to $150 per symbol per month; ORATS history from 2007 (price n/f); Intrinio EOD options $2,800 a year | n/a | n/a |

### Per class: the six questions in brief
**(a) Options premium.**
1. **Claim:** selling index options earns a premium beyond beta. Grade MIXED leaning NOT SUPPORTED post-2010: the long-run premium is SUPPORTED by Cboe and Israelov-Nielsen, and the post-2010 alpha is about 0 (Dew-Becker-Giglio). The 2023 VRP widening (Cboe) is the counter-evidence.
2. **Against T-bills:** PUT since 2007 earned about 6.7% against T-bills of about 1.3% (2007 onward, our estimate). Nearly all of the excess is 0.5 to 0.65 × the equity premium. Alpha after beta is about −0.2 to +0.7 pp.
3. **Tail:** the worst losses are crash months: October 2008, March 2020 and February 2018 (for short vol). They come exactly when the core also falls. Monthly magnitudes were not verified.
4. **Feasibility:** see Answer item 8. Tax:
   - XSP and SPX: §1256, 60/40, marked to market at year end.
   - SPY and single-stock options: short-term on short premium. Wash sales apply.
   - Non-qualified (deep in-the-money) covered calls can trigger the straddle rules and suspend the stock's holding period.
   - A tight collar can approach a constructive sale (§1259; not verified here).
   - At Alpaca, assignment cannot create short stock, and at-risk positions are auto-liquidated near expiry.
5. **Data:** Cboe's index series (BXM, BXMD, PUT, WPUT, CLL) are free and daily. They show the strategy's beta and drawdown, but they assume idealised fills at settlement prices, with no spread, so they cannot show a retail implementation's net. An honest test needs chains with bid/ask: ThetaData $40 to $160 a month, ORATS (from 2007), Cboe DataShop or Intrinio. Alpaca's own options history is short (start date UNVERIFIED).
6. **Bets per year:** about 12 (monthly) or 52 (weekly) per year, highly correlated through the volatility regime. At the historical vol-component Sharpe of about 1.0, t ≈ 2 takes about 4 years. At the post-2010 estimate of about 0, it never gets there.

**(b) Merger arbitrage.**
1. **Claim:** cash-deal spreads pay a premium over T-bills. Grade MIXED: SUPPORTED pre-1998 (Mitchell-Pulvino); post-2010 only fund records, about +1.7 pp (MERFX) or about 0 (MNA). Rate sensitivity: spreads are set over the risk-free rate, so gross returns rise with rates, but the excess is what counts. Lyxor estimated compression of over 500 bp during the falling-rate decade (press, snippet).
2. **Against T-bills:** about 0 to +1.7 pp, beta about 0.1 to 0.2.
3. **Tail:** March 2020 (MNA −16.7%, against the S&P −19.9% in Kiplinger's window), deal breaks, and antitrust or China-approval risk (GMO, 2021).
4. **Feasibility:**
   - MNA and MRGR are ETFs, buyable at Alpaca.
   - A do-it-yourself long-only cash-deal book is mechanically feasible: one market buy per deal, and cash arrives at completion. Paper simulation of a cash completion is UNVERIFIED (IC-B3).
   - At $10,000 it holds 5 to 10 deals, so one break of −20 to −40% costs $200 to $800.
   - Gains are short-term in most cases.
5. **Data:** deal filings (8-K 1.01, DEFM14A, SC TO-T, 425) and offer terms. They are free on EDGAR but not held (IC-B3).
6. **Bets per year:** about 10 to 40 deals a year at retail size. With the card's n_eff of 160, an honest read takes 4 to 16 years.

**(c) Trend.**
1. **Claim:** a diversified trend premium persists. Grade MIXED: long-history in-sample work (MOP/HOP; TT-1) against 2009-2019 at about Sharpe 0 and the 2024-25 record drawdown; 2022 is the strong post-2015 positive.
2. **Against T-bills:** about 0 to +4 pp in 2022 to 2025, with beta about 0.
3. **Tail:** 12 months of −18.7% (2024-25), from trend reversals across rates, currencies and equities.
4. **Feasibility:**
   - ETFs at Alpaca: yes. Futures: no (Alpaca has none, per idea-pipeline.md).
   - Tax: DBMF-type ETFs pay no K-1 but distribute ordinary income (UNVERIFIED).
   - A self-run ETF book (long/flat over about 10 asset-class ETFs) can be run, but it is not the futures product. PR #3682 found that ETF rules cut drawdown without beating SPY in return. Against T-bills, its beta is not zero.
5. **Data:** ETF bars (held for US ETFs); SG Trend and BTOP50 indexes are free monthly.
6. **Bets per year:** a handful of independent trend episodes a year. At Sharpe 0.3 to 0.5, t = 2 takes 16 to 44 years.

**(d) Market-neutral factor.**
1. **Claim:** a retail-reachable factor fund beats T-bills. Grade MIXED for QMNIX (closed); NOT SUPPORTED for BTAL. The big-cap factor mix earned +0.9% a year gross since 2010, t 0.66 (LS-2, UNVERIFIED).
2. **Against T-bills:** QMNIX about +5 pp (including post-boundary years); BTAL below T-bills.
3. **Tail:** 2018-2020 (QMNIX −38.8%). For BTAL, the 2023 and 2025 rallies (−15% and −20%).
4. **Feasibility:** BTAL yes. QMNIX no. A homemade book needs shorts at ≥$2k margin with no rebate (LS-17).
5. **Data:** French factors are free.
6. **Power:** low.

**(e) Event long/short (officer purchase clusters, large-move reversal).** Against T-bills, a hedged event book returns about α + (1 − β)·rf (LS-13). Its α did not clear the needed size against SPY (PR #3693, #3691), and Alpaca pays no rebate on short proceeds (LS-17). Re-reading it against T-bills adds nothing. NOT SUPPORTED.

**(f) Carry.**
- **T-bill ladder:** the baseline. Its return is the T-bill rate with zero beta.
- **Term premium:** negative for much of 2016 on (ACM). TLT −48%. NOT SUPPORTED post-2015.
- **Credit:** not searched (budget). INSUFFICIENT in this report. Credit spreads widen in equity crashes, so a credit sleeve is expected to carry hidden equity beta (UNVERIFIED).

**(g) CEF discounts.** INSUFFICIENT: no post-2010 net study was found. An equity CEF carries full equity beta unless its NAV is hedged.

**(h) Volatility products.**
- **Short VIX ETPs:** NOT SUPPORTED. The −96% day in 2018 is the failure case, and a satellite cannot survive it.
- **Long-vol hedges:** a paid insurance premium, negative expected return.

**(i) Pairs.** NOT SUPPORTED (LS-10).

**(j) Other.** A box spread on SPX or XSP is a way to earn about the T-bill rate with §1256 treatment. It is not a return source, and Alpaca support for it is UNVERIFIED. No other reachable class with a documented post-2015 non-beta net return was found, but this search was narrow (see Caveats).

## Disconfirmation
- **Searches run:**
  - BXM and PUT since 2007/2010 against their long history (Cboe factsheet, First Trust).
  - The Israelov-Nielsen decomposition, and critics on CAPM alpha for option strategies.
  - VRP decline (Dew-Becker-Giglio 2025) and option-ETF crowding (an AFA WP against Cboe's 2023 VRP widening).
  - 0DTE (Beckmeyer et al.; the HKUST 2020-24 study).
  - Trend in 2009-2019 (Gresham, AQR, Kurth et al.) and in 2023-2025 (SG Trend −18.7%; DBMF and KMLM annual returns).
  - Merger arbitrage spread compression (Jetley-Ji; Analysis Group; Lyxor) and post-2010 fund records.
  - The 2018-2020 quant drawdown (QMNIX annual returns).
  - BTAL's long-run loss.
  - XIV in 2018.
  - Term premium and the TLT drawdown.
  - CEF post-2010 net evidence (none found).
- **What was found against:**
  - Option alphas about 0 for 15 years.
  - Covered calls are mostly beta.
  - The 0DTE seller's net Sharpe is insignificant.
  - Trend had a decade at Sharpe about 0 and a record drawdown in 2024-25.
  - Merger spreads were down 400 to 500 bp, and the index ETF earned about the T-bill rate.
  - QMNIX had a −38.8% drawdown.
  - BTAL, short VIX and long Treasuries lost money.
- **Not run:** credit carry; long-vol hedge cost; Israelov's "Pathetic Protection" (not surfaced by search); a direct post-2015 put-write CAPM regression.

## Caveats & gaps
- **Fetch blocks.** Every external figure is from search summaries. Hosts blocked: docs.alpaca.markets, alpaca.markets, www.aqr.com, arxiv.org, ideas.repec.org, abramofranchetti.github.io. They must be allowed for verification. Status INCOMPLETE.
- **Our arithmetic.** The alpha-after-beta figures use assumed betas (0.5 to 0.65) and our own T-bill averages (about 2.2% for 2016-2025 and about 1.3% from 2007; about 4% for 2022-2025). Neither was pulled from a series.
- **Different windows.** Fund-record windows differ by source and snapshot date, and some include 2026, which is after the development boundary (ADR 0016). They are context, never out-of-sample evidence for this project.
- **Index series are not trades.** Cboe index returns have no trading costs. A retail 1-lot pays the option spread, often several percent of the premium on single-stock options.
- **Scope.** The charter excludes options and leverage, and options need their own ADR. This report changes neither.
- **Privacy incident.** During setup one stray fetch of a github.com organisation page used the project's name in the URL. It returned nothing useful.

## UNVERIFIED items
- Every row of the Evidence table except the repo probe.
- Alpaca's live options levels, the index-option launch date, the naked-index-short restriction, the CSP collateral rule, the fees, the paper NTA timing and the XSP paper settlement bug.
- That Alpaca cannot buy mutual funds (MERFX).
- DBMF's tax form.
- Today's T-bill rate.
- The period of MERFX's −20.8% drawdown.
- The §1259 collar point.

## Follow-up questions (not answered here)
- With docs.alpaca.markets allowed: on a live account, does Alpaca allow cash-secured XSP puts, XSP verticals and iron condors, and 4-leg boxes? What are the exchange fees per XSP contract, and has the paper XSP settlement bug been fixed?
- A registered pull of free Cboe index series (PUT, BXM, BXMD, WPUT) and SPY total return, 2016 to 2023, to measure beta and alpha against a T-bill series inside the store. This would replace the assumed-beta arithmetic.
- Credit carry and long-vol hedge cost (not searched).
- Whether an ADR 0005 amendment (a T-bill benchmark for a satellite book) should precede any test. That is the owner's decision.

## Sources
All accessed 2026-11-21, via web search summaries unless stated.
- [S1] Cboe, "Benchmark Indices Series: Income Generation and Smoother Returns with BXM, BXMD, PUT and CMBO": https://www.cboe.com/insights/posts/benchmark-indices-series-income-generation-and-smoother-returns-with-cboes-bxm-bxmd-put-and-cmbo-indices
- [S2] Cboe PUT factsheet: https://cdn.cboe.com/resources/indices/factsheet/CboeGlobalIndices_PUT-Index.pdf
- [S3] Israelov and Nielsen, "Covered Calls Uncovered", FAJ 71(6) 2015: https://rpc.cfainstitute.org/research/financial-analysts-journal/2015/covered-calls-uncovered; "Covered Call Strategies: One Fact and Eight Myths", FAJ 70(6) 2014: https://www.aqr.com/-/media/AQR/Documents/Insights/Journal-Article/FAJ-Covered-Call-Strategies-One-Fact-and-Eight-Myths.pdf (host blocked)
- [S4] Dew-Becker and Giglio, "The Decline of the Variance Risk Premium", Chicago Fed WP 2025-17: https://www.chicagofed.org/publications/working-papers/2025/2025-17; SSRN https://papers.ssrn.com/abstract=5525882; NBER w31833 https://nber.org/papers/w31833
- [S5] Cboe, "Are Option Income Funds Suppressing Volatility?": https://www.cboe.com/insights/posts/are-option-income-funds-suppressing-volatility
- [S6] AFA working paper on option-ETF demand: https://afajof.org/management/viewp.php?n=195748
- [S7] First Trust, "An update on covered call returns" (2026-03-24 and 2019 posts): https://www.ftportfolios.com/Commentary/MarketCommentary/2026/3/24/an-update-on-covered-call-returns
- [S8] Beckmeyer, Branger and Gayda (2023), "Retail Traders Love 0DTE Options… But Should They?", via CXO Advisory https://www.cxoadvisory.com/individual-investing/retail-0dte-option-trader-performance and CFR WP https://www.cfr-cologne.de/download/workingpaper/cfr-25-09.pdf
- [S9] HKUST Business Insight, "Are 0DTE options mispriced?" (2026-04): https://bm.hkust.edu.hk/bizinsight/2026/04/are-0dte-options-mispriced
- [S10] Babbedge and Kerson (2019), "Trend's not dead": https://www.cmegroup.com/education/files/trends-not-dead-babbedge-kerson-2019.pdf
- [S11] AQR, "You Can't Always Trend When You Want": https://www.aqr.com/Insights/Research/Journal-Article/You-Cant-Always-Trend-When-You-Want (host blocked)
- [S12] Kurth, Eisler, Rej and Bouchaud, "Is Trend Still Your Friend?", arXiv 2607.01550: https://www.alphaxiv.org/abs/2607.01550.md
- [S13] Advisor Analyst / Virtus AlphaSimplex on SG Trend: https://advisoranalyst.com/?p=167192; Hedgeweek H1 2025: https://www.hedgeweek.com/aqr-outpaces-trend-following-peers-with-double-digit-h1-gains/
- [S14] ETF Replay DBMF https://www.etfreplay.com/etf/dbmf, KMLM https://www.etfreplay.com/etf/kmlm; PortfoliosLab managed futures https://portfolioslab.com/portfolio/txcu7pwbeu9qictx25s9zdid
- [S15] Jetley and Ji (2010), FAJ 66(2): https://rpc.cfainstitute.org/research/financial-analysts-journal/2010/the-shrinking-merger-arbitrage-spread-reasons-and-implications; Analysis Group update https://www.analysisgroup.com/Insights/ag-feature/merger-arbitrage-spread---still-shrinking-/
- [S16] PortfoliosLab MERFX https://portfolioslab.com/tools/stock-comparison/MRGR/MERFX; PortfoliosLab MNA https://portfolioslab.com/symbol/MNA; Kiplinger MNA https://www.kiplinger.com/article/investing/T022-C009-S002-iq-merger-arbitrage-is-all-about-the-deals.html
- [S17] AQR QMNIX page https://funds.aqr.com/funds/aqr-equity-market-neutral-fund; PortfoliosLab https://portfolioslab.com/tools/stock-comparison/AQMIX/QMNIX
- [S18] PortfoliosLab BTAL vs SPY https://portfolioslab.com/tools/stock-comparison/BTAL/SPY; AOL https://www.aol.com/articles/anti-beta-etfs-btal-worth-143913443.html
- [S19] BIS Quarterly Review, March 2018: https://www.bis.org/publ/qtrpdf/r_qt1803t.htm; Nasdaq https://www.nasdaq.com/articles/volatility-spike-triggers-inverse-etf-xiv-closure-2018-02-07; Augustin, Cheng and Van den Bergen, "Volmageddon", FAJ 2021 https://rpc.cfainstitute.org/research/financial-analysts-journal/2021/volmageddon-failure-short-volatility-products
- [S20] NY Fed Liberty Street (ACM term premium): https://libertystreeteconomics.newyorkfed.org/2017/03/from-the-vault-factor-this-in/; PortfoliosLab TLT drawdown https://portfolioslab.com/portfolio/tjpjegwa60f2tfgog38kr0pz
- [S21] Flynn (2005) https://www.cirje.e.u-tokyo.ac.jp/research/workshops/micro/documents/flynn.pdf; Bradley, Brav, Goldstein and Jiang https://drupalgsb-test.paas.cc.columbia.edu/sites/default/files-efs/pubfiles/4118/activist_arb.pdf; ICI https://www.ici.org/25-view-academic-literature-support-cef-activism
- [S22] Alpaca options docs and support (host blocked): https://docs.alpaca.markets/docs/options-trading, https://docs.alpaca.markets/docs/options-level-3-trading, https://alpaca.markets/support/what-are-the-fees-associated-with-options-trading
- [S23] Alpaca index options: https://alpaca.markets/blog/alpaca-launches-index-options-via-trading-api, https://docs.alpaca.markets/us/docs/index-options, Finance Magnates https://www.financemagnates.com/institutional-forex/alpaca-moves-index-options-from-paper-trading-to-live-api-access/
- [S24] Alpaca 0DTE/expiration FAQ https://docs.alpaca.markets/us/docs/options-expiration-0-dte-faq; NTA doc https://docs.alpaca.markets/docs/non-trade-activities-for-option-events; forum https://forum.alpaca.markets/t/paper-trading-bugs-with-index-option-settlement/19441
- [S25] 26 USC 1256 https://www.law.cornell.edu/uscode/text/26/1256; 26 USC 1092 https://www.law.cornell.edu/uscode/text/26/1092
- [S26] ThetaData https://thetadata.net/subscribe; Cboe DataShop https://datashop.cboe.com/option-trades-with-calculations; ORATS https://orats.com/near-eod-data; Intrinio https://intrinio.com/thea
- Repo: [beyond-long-only](2026-10-10-beyond-long-only.md) (LS-2, LS-10, LS-13, LS-17, QMNIX); [trend-timing](2026-09-25-trend-timing.md) (TT-1); [idea-cards](idea-cards.md) IC-B3; [Alpaca sandbox probe](2026-10-19-alpaca-sandbox-probe.md); [ADR 0005](../decisions/0005-objective-benchmark-stop-criteria.md); charter scope line; this week's PRs #3682, #3678, #3693, #3691 and #3683 (cited as summarised in the brief, not re-read).

## Proposed claims
These are proposed for `claims.toml` with the prefix UC-. None is written to the register by this report.

| ID | Statement (short) | Kind | Grade | Scope / note |
|---|---|---|---|---|
| UC-1 | Index put-write and buy-write earn a return beyond their equity beta (alpha over T-bills) after 2010 | empirical | MIXED (leaning NOT SUPPORTED) | Cboe PUT/BXM against Dew-Becker-Giglio 2025 ("alphas indistinguishable from zero" over 15 years); Cboe VRP widening in 2023 against; snippet-level |
| UC-2 | A covered call keeps about 0.6 of equity beta; the short-vol part had Sharpe near 1.0 but about 10% of risk | empirical | UNGRADED (single source, abstract) | Israelov-Nielsen FAJ 2014/2015, historical to about 2014 |
| UC-3 | Retail 0DTE option buyers lose money on aggregate, and systematic short-0DTE strategies have economically insignificant net Sharpe (2020-24) | empirical | MIXED | Beckmeyer et al. 2023 (buyers); HKUST-summarised study (sellers) |
| UC-4 | Managed-futures trend earned a positive return over T-bills with near-zero equity beta after 2015 | empirical | MIXED | 2022 gains against a 2009-19 Sharpe of about 0 and SG Trend's −18.7% in the 12 months to April 2025 |
| UC-5 | Merger arbitrage earns a premium over T-bills after 2010 net of fees | empirical | MIXED | MERFX about +1.7 pp a year against MNA about 0 (10y); spreads down >400 bp since 2002 |
| UC-6 | Retail anti-beta (BTAL) earns a positive return over T-bills | empirical | NOT SUPPORTED | about −1%/yr since 2011; a negative-beta hedge |
| UC-7 | Short VIX ETPs are unusable for a satellite book: XIV lost 96.3% in one day (2018-02-05) and was terminated | fact | SUPPORTED (BIS plus press; snippet) | — |
| UC-8 | Alpaca paper accounts have options level 3, fill 2-leg `mleg` orders as a unit, and list SPY options as American with a 100 multiplier and integer quantity | fact | SUPPORTED (repo probe 2026-10-19) | paper only |
| UC-9 | Alpaca live: level 1 covered calls and cash-secured puts, level 3 by application, $0 commission; index options (SPX, XSP) live via API, European and cash-settled, with no naked index shorts at launch | fact | INSUFFICIENT (snippets only; host blocked) | verify on docs.alpaca.markets |
| UC-10 | Broad-based cash-settled index options (SPX, XSP) are §1256 contracts taxed 60/40 and marked to market; ETF and equity options are not | fact | UNGRADED (statute via snippet) | 26 USC 1256, Pub. 550 |
| UC-11 | At $10,000, the realistic non-beta excess of the best-evidenced classes (trend, merger arbitrage) is about $0 to $300 a year, with worst cases of about −$1,700 to −$2,100 | derived (arithmetic) | UNGRADED | from UC-4 and UC-5 inputs; all inputs UNVERIFIED |
| UC-12 | The long Treasury term premium paid nothing post-2015: ACM negative for much of 2016 on, and TLT fell −48% from 2020-08 to 2023-10 | empirical | NOT SUPPORTED (as a positive premium post-2015) | NY Fed plus aggregator |
