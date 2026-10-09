# Research Report: Short-horizon candidates for the paper books

**Brief:** #1353 (from the owner's direction #1352)  ·  **Date:** 2026-10-09  ·  **Status:** COMPLETE  ·  **Agent/model:** team swingideas, claude-opus-5-5

**Question.** Which three to five strategies with holding periods of days to a few weeks are the best next candidates for TradePartner's paper books, given the data we hold (daily unadjusted SIP bars from about 2019, corporate actions, the EDGAR filing record with acceptance times, five as-filed statement facts, Form 25 delistings; no intraday, options, short sales, leverage or Form 4 transactions), the ADR 0006 universe (top ~1000 US common stocks), a ~$100k paper and ~$100 live account, and the after-open fill costs of the Probe 3 report?

**Decision it feeds.** Which two or three candidates get hypothesis files next (#1352). This report grades evidence; it writes no hypothesis file and changes no backlog decision.

## Answer
**Verdict:** MIXED  ·  **Confidence:** medium for the negatives, low for the positives

No candidate at this horizon is SUPPORTED for a long-only, top-1000 book after costs and after publication. Most of the short-horizon literature fails in exactly our setting. **Plain short-term reversal** (monthly; weekly not tested post-2009, see De Groot et al.) and **industry momentum** (one-month and six-month) are NOT SUPPORTED in large caps after 2001, and costs alone erase them (SH-1, SH-2, SH-3). **Analyst- or random-walk-surprise PEAD** is NOT SUPPORTED outside microcaps since 2006 (SH-5, confirming HO-4). The **earnings-announcement premium** is INSUFFICIENT: the one working paper found says it has been gone in the US since 2004 (SH-8). **Insider purchases at a days horizon** stay NOT SUPPORTED (G2-A), and we hold no Form 4 transactions. Two candidates (EAR drift, IRRX) keep a post-publication signal in value-weighted or liquidity-screened data and are graded MIXED; short-term momentum has only in-paper evidence to 2018 and is graded INSUFFICIENT:
1. **Earnings-announcement-return (EAR) drift.** Buy the stocks with the strongest three-day market-adjusted return around the earnings release, then hold for about a month (SH-6, SH-7).
2. **Short-term momentum.** Buy last month's winners among high-turnover large caps, rebalanced monthly (SH-9).
3. **Announcement-adjusted, industry-relative reversal.** Monthly, best in low-volatility stocks (SH-4).

EAR drift and IRRX have a gross long-short edge of about 0.2 to 0.8% a month after 2001; short-term momentum's is 0.42% a month in the largest 500 (in sample). The long leg alone is a fraction of that. At the backtester's default 15 bp per side, a full monthly turnover costs about 0.30% a month, which is roughly the whole long-leg edge. "A real chance of surviving costs" therefore depends on fills well below that default. Probe 3's median of +6 bp on whole-share fills was measured on two megacaps against the official open, not the SIP bar open the backtester fills at (P3-5), so it does not establish such fills for a top-1000 book.

## Ranked shortlist (by evidence and information value per unit of cost, for the owner's choice)

| Rank | Candidate | Grade | Gross edge (sample) | Long-only, our scale, after costs | Trades / month (top-1000, long-only) | Data we lack |
|---|---|---|---|---|---|---|
| 1 | EAR drift: buy the top EAR quintile when the [-1,+2] window closes, hold ~20 sessions | MIXED | VW long-short 0.36%/mo, t 2.09 (Dai et al., 2001-2021); 0.81%/mo, t 3.12 (CZ VW deciles, 2010-2024); 0.25%/mo, t 2.15 (CZ, ex-bottom-20% NYSE size, 2010-2024) | Long leg over the average portfolio +0.32%/mo (VW) to +0.07%/mo (size-screened), gross. Net at 15 bp/side ≈ 0 to −0.2%/mo; positive only near 5 bp/side | ~65 entries + ~65 exits (≈4,000 announcements a year in the universe; top quintile) | **Earnings-release time**: 8-K Item 2.02 acceptance (EDGAR submissions `items` field; our adapter reads the payload but does not keep it). Fallback: 10-Q/10-K acceptance (held), a later and untested event |
| 2 | Short-term momentum: long last-month winners in the highest-turnover names, monthly | INSUFFICIENT | Largest-500 long-short 0.42%/mo, t 4.91, gross (Medhat & Schmeling, 1963-2018); full-sample net of half-spreads 1.00%/mo | Long leg alone not reported; no post-2018 US test found | ~40 entries + ~40 exits (one-sided turnover ~90%/mo) | None: prices, volume and shares outstanding are held |
| 3 | Announcement-adjusted industry-relative reversal (IRRX), low-volatility half, monthly | MIXED | VW long-short 0.58%/mo, t 3.29, gross (Dai et al., 2001-2021); low-vol industry-relative reversal net 0.19%/mo, t 1.41 (Novy-Marx & Velikov, to 2013) | Long leg alone not reported; net long-short is insignificant even in the best published construction | ~90 entries + ~90 exits (quintile of ~500 low-vol names, ~90% turnover) | Earnings-release time (as rank 1); SIC is held |

**Not recommended at this horizon:** plain monthly reversal (weekly untested post-2009), industry momentum, SUE-based PEAD, revenue-surprise drift alone, the earnings-announcement premium, the dividend-month premium, insider purchases (days horizon), and 10-K-filing drift. The reasons are in the Evidence table.

**Proposed for hypothesis files: ranks 1 and 2.** Rank 1 uses the data edge the brief names: exact acceptance times, here of the 8-K earnings release. It has the most independent post-publication support, and it produces the most trades. Rank 2 needs no new data and is the cheapest test. Rank 3 shares rank 1's data need and has the weakest net evidence, so it is a variant to consider after rank 1, not a first file.

**On "keyed on 10-Q/10-K acceptance".** The brief's framing does not hold as stated. The surprise reaches the price at the earnings release, not at the 10-Q. Example (EDGAR submissions, fetched 2026-10-09): Apple's 8-K Items 2.02/9.01 were accepted at 2026-07-31T00:30:28Z and its 10-Q at 14:01:02Z the same day. That 10-Q arrived about 13.5 hours after the release, inside the [-1,+2] window. For other issuers the 10-Q comes days or weeks later. Martineau (SH-5) finds that since 2006 all price discovery after analyst surprises happens at the announcement in non-microcaps. So a signal keyed on the 10-Q measures a different and untested event (ER-6 MIXED). The usable data edge is the 8-K 2.02 acceptance timestamp. It is free, and our adapter already downloads the payload that carries it.

## Evidence

Grades follow the handoff §6.1 protocol. Tier: T1 is a peer-reviewed paper, an academic working paper, an official source or a replication dataset. "CZ" means TradePartner's own read-only computation on the Chen & Zimmermann (CFR 2022) open-source portfolio returns, release downloaded 2026-10-09 through the `openassetpricing` package. Portfolios are monthly, gross and long-short as published. "Long leg" means the top portfolio minus the mean of all of that signal's portfolios, a rough long-only proxy. Numbers are % per month with the t-statistic in brackets. That computation is in scratch, outside the repo, and nobody else has checked it (see UNVERIFIED).

| Claim | Source | Tier | Key figure (quoted) | OOS / post-pub? | Net of costs? |
|---|---|---|---|---|---|
| **SH-1** Plain one-month reversal has no edge in large caps after publication | Medhat & Schmeling, RFS 2022 | T1 | "The largest-500 STREV* strategy, however, yields just 2 basis points per month" (§2.5; 1963-2018) | partly (sample to 2018) | gross |
| SH-1 | CZ `STreversal`, VW deciles | T1 data, own computation | post-publication (1991-2024) LS −0.05 [−0.11]; 2010-2024 −0.58 [−1.08]; size-screened 2010-2024 0.25 [0.70], long leg −0.16 [−0.59] | yes | gross |
| SH-1 | Dai, Medhat, Novy-Marx & Rizova, working paper 2023 | T1 | post-decimalization (2001-2021) REV "only 18 bps/month" (Table A2: 0.18 [0.62]) | yes | gross |
| **SH-2** Costs exceed reversal and industry-momentum gross spreads | Novy-Marx & Velikov, RFS 2016 | T1 | Table 3 Panel C (VW): short-run reversals gross 0.37 [1.71], net −1.28 [−6.02]; industry momentum net −0.29 [−1.20]; industry-relative reversals net −0.80 [−4.73]; "only the High-frequency Combo and the Low Volatility Industry Relative Reversals strategies achieving positive net excess returns" | partly | yes (effective spreads, which are biased upward after 2003 per Chen & Velikov) |
| SH-2 | Chen & Velikov, JFQA 2023 | T1 | "Net of these effects, the average anomaly's expected return is 4 basis points per month" (published abstract; the 2020 FEDS draft says 8 bps) | yes | yes |
| SH-2 (counter) | De Groot, Huij & Zhou, JBF 2012 | T1 | reversal "generate[s] 30-50 basis points per week net of trading costs" in large caps with cost-aware construction | no (sample ends 2009) | yes (long-short, institutional) |
| SH-2 (counter) | Blitz, Hanauer, Honarvar, Huisman & van Vliet, FAJ 2023 (QI brief ref 73) | T1/T2 (practitioner-affiliated) | short-term signals turn over "between 1,300% and 2,000% per annum"; "break-even trading cost levels … are all below 25 basis points"; positive net alpha only by combining signals with efficient trading rules | partly | yes |
| **SH-3** Industry momentum (one-month and six-month) is gone after 2001 | Dai et al. 2023 | T1 | IMOM 0.68 [3.57] for 1973-2021, but 0.09 [0.34] for 2001-2021; "the returns to IMOM are negligible post-decimalization" (App. A.1) | yes | gross |
| SH-3 | CZ `IndMom` (6-month industry signal) | T1 data, own computation | post-publication (2000-2024) VW LS 0.06 [0.18]; 2005-2024 −0.01 [−0.02] | yes | gross |
| **SH-4** Announcement-adjusted industry-relative reversal survives gross after 2001 | Dai et al. 2023 | T1 | IRRX 0.58 [3.29] for May 2001 to Dec 2021, value-weighted, long-short | yes | gross only |
| SH-4 (against) | Novy-Marx & Velikov 2016 | T1 | low-vol industry-relative reversal net 0.19 [1.41], FF4 net alpha 0.07 [0.57] | partly | yes |
| **SH-5** SUE-based PEAD is gone outside microcaps | Martineau, CFR 2022 | T1 | "since 2006, … analyst earnings surprises fail to positively predict post-announcement returns over 60 days for all-but-microcap stocks"; random-walk surprises predict only "prior to 1990"; Table 3 Panel B, BHAR[2,60] slope on the surprise rank 2016-2019 −0.002** | yes | gross |
| SH-5 | CZ `EarningsSurprise` | T1 data, own computation | post-publication (1985-2024) VW LS −0.06 [−0.52]; 2019-2023 −0.09 [−0.26] | yes | gross |
| **SH-6** Drift after the earnings-announcement return persists after publication, smaller | CZ `AnnouncementReturn` (Chan, Jegadeesh & Lakonishok 1996; [-1,+2] market-adjusted return around the I/B/E/S date) | T1 data, own computation | VW deciles: in-sample LS 1.34 [7.56]; post-publication (1997-2024) 0.99 [4.47]; 2010-2024 0.81 [3.12], long leg 0.32 [1.88]; 2019-2023 0.66 [1.20]. Size-screened: 2010-2024 0.25 [2.15], long leg 0.07 [0.94] | yes | gross |
| SH-6 | Dai et al. 2023 | T1 | PEAD (three-day CAR around the latest announcement) 0.36 [2.09] for 2001-2021, VW long-short | yes | gross |
| SH-6 | Novy-Marx & Velikov 2016 | T1 | PEAD (CAR3) gross 0.91, one-sided turnover 34.7%/mo, net 0.34 (significant; one of the few mid-turnover strategies "statistically significantly larger than zero" net) | partly (sample to 2013) | yes |
| SH-6 | Brandt, Kishore, Santa-Clara & Venkatachalam, 2008 (working paper) | T1 | EAR and SUE strategies earn "about 12.5% per annum" (secondary summary; figure **UNVERIFIED** against the paper) | no | gross |
| **SH-7** Revenue surprise alone is weak after publication | CZ `RevenueSurprise` (Jegadeesh & Livnat 2006) | T1 data, own computation | post-publication (2007-2024) VW LS 0.34 [1.68]; 2019-2023 −0.29 [−0.68]; size-screened 2010-2024 0.22 [1.54] | yes | gross |
| **SH-8** The US earnings-announcement premium has gone since 2004 | Heitz, Narayanamoorthy & Zekhnini, working paper 2020 | T1 | announcer premium pre-2004 "32.6 bps" a week (VW) against post-2004 "0.3 bps (8.7 bps)", "not statistically different from zero" (Table 2) | yes | gross |
| **SH-9** Short-term momentum in high-turnover large caps | Medhat & Schmeling, RFS 2022 | T1 | "survives transaction costs and is strongest among the largest, most liquid"; largest-500 STMOM 0.42 [4.91]; megacap STMOM 0.53 [2.53]; full-sample net of half-spreads 1.00 [3.47]; one-sided turnover 89-90%/mo; extends to 23 developed markets | in-paper international OOS; no post-2018 US test found | yes (half-spreads, long-short) |
| SH-9 (against) | "Does short-term momentum exist in China?" (Otago / XJTLU) | T1 | finds "no short-term momentum but only short-term reversal" in China (abstract only) | n/a (other market) | — |
| **SH-10** The dividend-month premium is not visible after publication | Hartzmark & Solomon, JFE 2013 | T1 | predicted-dividend-month abnormal return 53 bp/mo against all firms, 37 bp/mo against non-month payers (1927-2011) | — | gross |
| SH-10 | Ainsworth et al., "Can dividend schedules predict abnormal returns?" (working paper) | T1 | US 1993-2013: four-factor alpha "75 basis points per month" | 2 post-publication years | gross |
| SH-10 (against) | CZ `DivSeason` (size-screened; not in the VW decile file) | T1 data, own computation | in-sample LS 0.26 [11.76]; post-publication (2014-2024) 0.01 [0.21]; 2010-2024 −0.00 [−0.04] | yes | gross |
| G2-A (existing) | G2 insider report | T1 | most-liquid quartile "+0.03% (t 0.37)" to the next close, gross, 2018-2023 | yes | gross |
| **SH-11** 10-K-filing drift is a 12-month, pre-2005 effect | You & Zhang, RAS 2009 | T1 | good-news 10-K stocks +1.49% at 3 months, +4.25% at 12 months after filing; effect "only" in complex 10-Ks | no | gross |
| **SH-12** Earnings-release time is in the free EDGAR record | EDGAR submissions JSON for CIK 0000320193, fetched 2026-10-09 | T1 (primary) | `items` = "2.02,9.01" on the 8-K accepted 2026-07-31T00:30:28Z; 10-Q accepted 14:01:02Z the same day; the payload carries `acceptanceDateTime` and `items` per filing | — | — |

### Per-candidate notes (horizon, trades, data, decay)

- **Earnings-announcement-return (EAR) drift (rank 1).**
  - **Horizon:** 20 to 60 sessions. Martineau's BHAR[2,60] is the long window; CZ and Dai et al. hold one month.
  - **Trades:** about 4,000 announcements a year in a ~1000-name universe. The long-only top quintile gives ~65 entries a month, with exits on the same schedule.
  - **Decay:** about a quarter to a third of the in-sample spread is lost in VW data (1.34 to 0.99, then 0.81 after 2010). The size-screened version loses more (0.87 to 0.25). That is consistent with QI-2.
  - **Point-in-time design:** breakpoints come only from announcements whose [-1,+2] window closed before the entry (e.g. the trailing quarter's EAR distribution). Day 0 is the first trading-calendar session whose open is after the 8-K acceptance time. Entry is at the next open after day +2's close.
  - **Engine:** an event family, with entries keyed on an event time rather than a month-end (the "month_end-only guard" #1352 lists).
  - **Data:** the 8-K 2.02 acceptance, a small extension of the EDGAR adapter, and an adjusted-return series for the window, which is computable from bars plus corporate actions.
  - **Disconfirmation:** Martineau (SH-5) for analyst-SUE PEAD, and Chen & Velikov (SH-2) on the average anomaly.
- **Short-term momentum (STMOM, rank 2).**
  - **Horizon:** one month.
  - **Trades:** a long-only 5×5 corner in the top 1000 holds about 40 names, with ~90% one-sided turnover a month.
  - **Data:** turnover needs daily volume (in the bars) and shares outstanding (`EntityCommonStockSharesOutstanding`, held), used as known at its acceptance time and adjusted for splits whose ex-date is on or before the sort date; otherwise unadjusted volume over a pre-split share count mis-measures turnover around every split.
  - **Decay:** the published sample ends in 2018, and no independent US post-publication test was found.
  - **Disconfirmation:** China (reversal only) and SH-1's reminder that the sign of a one-month sort flips with turnover. Low-turnover names reverse, so a mis-measured turnover sort gives a reversal book.
- **Announcement-adjusted industry-relative reversal (IRRX, rank 3).**
  - **Horizon:** one month. Reversal decays within weeks, faster in high-volatility names (Dai et al. §3).
  - **Trades:** ~90 entries a month on the long side.
  - **Data:** earnings windows, as rank 1, and SIC industries (held).
  - **Disconfirmation:** Novy-Marx & Velikov's net 0.19 [1.41], not significant.
  - **Our fills:** large-cap daily price pressure has a half-life of about half a day (Hendershott & Menkveld, quoted in Medhat & Schmeling fn 12: "17 basis points with a half life of 0.54 days"). A daily or weekly reversal entered at the next open, after Probe 3's 17-118 s delay, misses most of it. Only the monthly, inventory-driven part is reachable.

## Disconfirmation
- **Searches run:**
  - Martineau "Rest in peace PEAD"
  - "earnings announcement premium disappeared"
  - Nagel "evaporating liquidity" (reversal as liquidity provision)
  - "short-term reversal after 2010 large caps / Khandani Lo"
  - Novy-Marx & Velikov taxonomy (net costs)
  - Chen & Velikov "Zeroing in" (net, post-publication)
  - "industry momentum post-publication"
  - "short-term momentum replication fails after 2018"
  - "dividend month premium after publication"
  - "PEAD large caps after 2010"
  - Hirshleifer, Peng & Wang (RFS 2025), the counterpoint named in HO-4
  - plus our own post-publication computation on the Chen & Zimmermann data for nine signals
- **What was found against:**
  - **Reversal, industry momentum, SUE-PEAD, the announcement premium and the dividend month:** each has a credible post-publication null in liquid or VW data (SH-1, SH-3, SH-5, SH-8, SH-10).
  - **Chen & Velikov:** the average anomaly earns ~4 bp/month net, post-publication, in the modern era. That is the prior for every candidate here.
  - **Hirshleifer, Peng & Wang** do not revive drift. They find weaker drift where news spreads faster.
  - **STMOM:** no US replication after 2018, and China shows the opposite.
  - **EAR drift:** nothing found that shows it gone. The size-screened CZ series shrinks to 0.25 [2.15] long-short and 0.07 [0.94] on the long leg, and 2019-2023 alone is insignificant in both files. That is the best available disconfirmation of rank 1. Sixty months have little power (QI-1), so it does not refute the signal.

## Caveats & gaps
- **Long-only is the binding constraint.** Every published number is long-short. The "long leg" proxies are crude: top portfolio minus the mean of the signal's portfolios, not minus SPY. For reversal and EAR, the short leg carries much of the spread in the published series.
- **Costs at our scale.**
  - The backtester charges 15 bp per side by default (`costs.per_side_bps`), with a ladder to 100.
  - Probe 3's whole-share paper fills sat −9.3 to +34.7 bp from the official open (median +6.2, n = 4), and the SIP bar open differs from the official open by −4.4 to +19.8 bp (P3-2, P3-5).
  - A monthly-turnover book pays about 0.30%/mo round trip at the default, so all three candidates are near zero net at 15 bp and positive only near 5 bp. 5 bp/side is below the handoff §D2 / HO-14 floor (5-10 bp plus half the spread); at that floor all three are net ≤ 0.
  - Paper fills are optimistic (HO-14), and live fractional fills are priced differently (AO-1).
- **In-sample power.** 2019-2023 is 60 months. None of the nine CZ series is significant in that window alone, including momentum, which is significant over 2010-2024. A test on our in-sample data can kill a candidate that is clearly negative, but it cannot confirm one (QI-1, QI-9). Paper books give many trades, but not the years of evidence a small edge needs.
- **Universe mismatch.** The CZ VW deciles span all CRSP stocks, value-weighted; the size-screened file drops names below the NYSE 20th percentile and follows each original paper's weighting. Neither is the ADR 0006 top ~1000, and the truth for our universe probably lies between them.
- **Event timing.** The CZ `AnnouncementReturn` uses I/B/E/S dates. Our replacement, the 8-K 2.02 acceptance, can differ: it misses releases not furnished on an 8-K and catches 2.02 items that are not quarterly results. One issuer was checked; the distribution of release-to-10-Q lags across the universe was not measured.
- **Taxes.** Monthly turnover makes every gain short-term (HO-20). Wash sales are frequent when a name re-enters within 30 days (G7). Neither matters on paper; both matter live.
- **Development boundary.** The CZ 1997-2024 and 2010-2024 windows include 2024, after the 2023-12-29 development boundary (ADR 0016). Candidate selection therefore saw published 2024 returns: 2024-2026 must never be cited as out-of-sample evidence for EAR drift or short-term momentum.
- Dai et al. (2023) and Heitz et al. (2020) are working papers. Collin-Dufresne & Daniel's reversal paper was read but not used: it is marked "Preliminary: please do not quote".

## UNVERIFIED items
- **All CZ figures are this team's own computation.** Source: the Chen & Zimmermann release downloaded 2026-10-09 (`deciles_vw` and `ex_nyse_p20_me` files; "long leg" defined above). No second party has checked it; the scripts and data sit in the session scratchpad, not the repo.
- Brandt et al.'s "12.5% per annum" is a secondary-source summary.
- Novy-Marx & Velikov's PEAD (CAR3) net t-statistic was not read: the table row was cut at extraction. The text says it is statistically significant.
- The trades-per-month figures are estimates from the universe size and one-sided turnover, not counts.
- The China short-term-momentum paper was read at abstract level only.

## Follow-up questions (not answered here)
- How many ADR 0006 issuers furnish quarterly results on an 8-K Item 2.02, and what is the distribution of the lag from that 8-K to the 10-Q/10-K? This is a read-only check on the cached submissions payloads, and it decides whether the 10-Q fallback is ever needed.
- Does the long leg of STMOM or EAR beat SPY, rather than the average portfolio, in the top 1000 over 2019-2023 at 5, 15 and 30 bp per side? Only our own backtest can answer this, and it would count as trials.
- The engine work #1352 names: entries keyed on an event time, several books at once. That scoping belongs to the code-blocker read, not to this report.

## Sources
1. Martineau, C. (2022). Rest in Peace Post-Earnings Announcement Drift. *Critical Finance Review*. https://cfr.ivo-welch.org/published/papers/martineau2021rest.pdf
2. Dai, W., Medhat, M., Novy-Marx, R., & Rizova, S. (2023, January). Reversals and the returns to liquidity provision. Working paper. https://mysimon.rochester.edu/novy-marx/research/RRLP.pdf
3. Medhat, M., & Schmeling, M. (2022). Short-term Momentum. *Review of Financial Studies* 35(3). Paper version: https://openaccess.city.ac.uk/id/eprint/31278/1/MS_short_term_mom_v27.pdf
4. Novy-Marx, R., & Velikov, M. (2016). A Taxonomy of Anomalies and Their Trading Costs. *Review of Financial Studies* 29(1), 104-147. https://www.nber.org/papers/w20721
5. Chen, A. Y., & Velikov, M. (2023). Zeroing In on the Expected Returns of Anomalies. *JFQA* 58(3), 968-1004. https://www.federalreserve.gov/econres/feds/zeroing-in-on-the-expected-returns-of-anomalies.htm
6. Chen, A. Y., & Zimmermann, T. (2022). Open Source Cross-Sectional Asset Pricing. *Critical Finance Review*. https://www.federalreserve.gov/econres/feds/open-source-cross-sectional-asset-pricing.htm (data release via the `openassetpricing` package)
7. De Groot, W., Huij, J., & Zhou, W. (2012). Another look at trading costs and short-term reversal profits. *Journal of Banking & Finance* 36(2), 371-382. https://ideas.repec.org/a/eee/jbfina/v36y2012i2p371-382.html
8. Blitz, D., Hanauer, M. X., Honarvar, I., Huisman, R., & van Vliet, P. (2023). Beyond Fama-French Factors: Alpha from Short-Term Signals. *Financial Analysts Journal*. https://rpc.cfainstitute.org/research/financial-analysts-journal/2023/beyond-fama-french-factors-alpha-from-short-term-signals
9. Nagel, S. (2012). Evaporating Liquidity. *Review of Financial Studies* 25(7), 2005-2039. https://www.nber.org/papers/w17653
10. Heitz, A. R., Narayanamoorthy, G., & Zekhnini, M. (2020, June 24). The Disappearing Earnings Announcement Premium. Working paper. https://www.iimb.ac.in/ARC2020/Papers/The_Disappearing_Earnings_Announcement_Premium.pdf
11. Hartzmark, S. M., & Solomon, D. H. (2013). The dividend month premium. *Journal of Financial Economics* 109(3), 640-660. https://ideas.repec.org/a/eee/jfinec/v109y2013i3p640-660.html
12. Ainsworth, A., et al. Can Dividend Schedules Predict Abnormal Returns? International Evidence. Working paper. https://acfr.aut.ac.nz/__data/assets/pdf_file/0003/29712/581064-A-Ainsworth-DividendSchedules_AFM.pdf
13. You, H., & Zhang, X. (2009). Financial reporting complexity and investor underreaction to 10-K information. *Review of Accounting Studies* 14(4), 559-586. https://ideas.repec.org/a/spr/reaccs/v14y2009i4d10.1007_s11142-008-9083-2.html
14. Hirshleifer, D., Peng, L., & Wang, Q. (2025). News Diffusion in Social Networks and Stock Market Reactions. *Review of Financial Studies* 38, 883-937. https://www.nber.org/papers/w30860
15. Moskowitz, T., & Grinblatt, M. (1999). Do Industries Explain Momentum? *Journal of Finance* 54(4). https://ideas.repec.org/a/bla/jfinan/v54y1999i4p1249-1290.html
16. Does short-term momentum exist in China? (Otago / XJTLU), abstract. https://scholar.xjtlu.edu.cn/en/publications/does-short-term-momentum-exist-in-china
17. Brandt, M., Kishore, R., Santa-Clara, P., & Venkatachalam, M. (2008). Earnings announcements are full of surprises. Working paper. Cited via secondary summary only.
18. SEC EDGAR submissions API: https://www.sec.gov/search-filings/edgar-application-programming-interfaces ; payload https://data.sec.gov/submissions/CIK0000320193.json (fetched 2026-10-09)
19. Repo context: [probe3-preopen-fills](2026-10-09-probe3-preopen-fills.md) (P3-2, P3-5), [g2-insider-purchases](2026-09-26-g2-insider-purchases.md) (G2-A, G2-B), [quantitative-investing brief](2026-10-03-quantitative-investing-research-brief.md) (QI-1, QI-2, QI-17), [initial handoff](2026-09-24-initial-research-handoff.md) (HO-4, HO-14, HO-20).
