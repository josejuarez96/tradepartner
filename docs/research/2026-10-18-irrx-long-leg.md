# Research Report: IRRX long leg, kill-ladder rung 2

**Brief:** #1734  ·  **Date:** 2026-10-18  ·  **Status:** INCOMPLETE  ·  **Agent/model:** team irrx, claude-opus-5-5

**Question.** The rung-2 question, written before the run, asks for the long leg of announcement-adjusted industry-relative reversal (IRRX; Dai, Medhat, Novy-Marx & Rizova 2023), or of the closest industry-relative-reversal series available. The long leg is the top portfolio minus the mean of the signal's portfolios. It is wanted for 2010-2024 and 2019-2024, value-weighted, and size-screened where available. **KILL** if the 2010-2024 long leg is below 0.10%/month **or** its t-statistic is below 1.0. Otherwise IRRX passes to rung 3.

**Decision it feeds.** Whether IRRX (claim SH-4, rank 3 of the short-horizon report) goes on to rung 3. This report grades evidence. It does not decide.

## Answer
**Verdict:** INSUFFICIENT  ·  **Confidence:** low  ·  **Rung 2: NOT DECIDED (neither KILL nor PASS)**

The rung-2 number could not be produced, for three reasons.
1. **No public series exists.** The Chen & Zimmermann (CZ) release has no industry-relative or announcement-adjusted reversal signal (IL-1).
2. **This session could not compute anything.** It had no shell or Python tool, so nothing was downloaded or computed. The scratch directory named in the brief was never created.
3. **Most of the web was unreachable.** The fetch tool failed DNS lookup (`getaddrinfo ENOTFOUND`) for every host tried except github.com and raw.githubusercontent.com: rochester.edu, nber.org, ssrn.com, repec.org, dimensional.com, wharton.upenn.edu, nus.edu.sg, alphaarchitect.com and cxoadvisory.com. So the published tables of Dai et al. could not be re-read either.

The repo's earlier report, drawing on the paper, says the IRRX long leg "is not reported" (SH-4).

The only computed related series is plain one-month reversal (CZ `STreversal`, from the 2026-10-09 run). Its size-screened 2010-2024 long leg is **−0.16%/month [t −0.59]** (SH-1). That figure would fail both kill thresholds. But it is not IRRX. Dai et al. find that the industry and announcement adjustments are what separate a dead signal from a live one after 2001: REV earns 0.18 [0.62] and IRRX 0.58 [3.29]. The proxy therefore cannot stand in for the kill test. Applying the kill rule to it would test SH-1 again, which is already NOT SUPPORTED.

## Evidence

Tiers follow handoff §6.1. "CZ" means the 2026-10-09 team computation on the Chen & Zimmermann portfolios. It has not been re-run or checked here (UNVERIFIED).

| Claim | Source | Tier | Key figure (quoted) | OOS / post-pub? | Net of costs? |
|---|---|---|---|---|---|
| **IL-1** CZ has no industry-relative or announcement-adjusted reversal signal | CZ `SignalDoc.csv` (OpenSourceAP/CrossSection, master, fetched 2026-10-18), all rows read in two passes | T1 (replication dataset docs) | Reversal-type rows: `STreversal` ("Short term reversal", Jegadeesh 1990, EW, decile, 1.99 [12.44]), `LRreversal`, `MRreversal`, `MomRev`, `MomOffSeason*`. Industry-type rows: `IndMom` ("Industry Momentum"), `IndRetBig` ("Industry return of big firms", Hou 2007), `retConglomerate`, `iomom_cust/supp`. No acronym contains "IRR", "IndRev" or "IndRR" | — | — |
| IL-1 | CZ `Signals/pyCode/Predictors/STreversal.py` and `IndRetBig.py` (fetched 2026-10-18; read through a summarizing fetch tool) | T1 | `STreversal` is the stock's raw last-month return with no industry adjustment. `IndRetBig` is the equal-weighted mean return of the largest 30% of firms by market cap in each FF48 industry, assigned to the smaller firms in that industry (an industry lead-lag signal, not stock-relative reversal) | — | — |
| **IL-2** Closest computed proxy (plain reversal) fails the rung-2 thresholds | CZ `STreversal`, size-screened (ex NYSE p20), 2026-10-09 computation (SH-1) | T1 data, own computation | 2010-2024 long leg **−0.16 [−0.59]**; long-short 0.25 [0.70]. VW deciles 2010-2024 long-short −0.58 [−1.08]; VW long leg not recorded. 2019-2024 not computed | yes | gross |
| **IL-3** The adjustments are what make IRRX differ from plain reversal | Dai et al. 2023 WP, via SH-1/SH-4 (Table A2 and the IRRX row, as quoted in the 2026-10-09 report; not re-read here) | T1 | 2001-2021 VW long-short: REV "only 18 bps/month" 0.18 [0.62], IRRX 0.58 [3.29] | yes | gross |
| IL-3 (net) | Novy-Marx & Velikov 2016 via SH-4 | T1 | low-vol industry-relative reversal net 0.19 [1.41], FF4 net alpha 0.07 [0.57] (sample to 2013) | partly | yes |
| **IL-4** No published long-leg decomposition of IRRX was found | 2026-10-09 report (SH-4 row: "Long leg alone not reported"); searches below | T1 (via repo) | — | — | — |

### Construction gap: what any CZ-based proxy would miss against Dai et al.'s IRRX
- **Industry adjustment.** IRRX ranks the stock's return minus its industry's return. Dai et al. use Fama-French industries, per the 2026-10-09 report (the exact industry count was not re-checked). `STreversal` ranks the raw return. `IndRetBig` is an industry-level variable, not a stock-minus-industry residual. A firm-level IRR cannot be built from CZ portfolio returns. It needs firm returns (CRSP), which the CZ public release does not redistribute.
- **Announcement adjustment.** IRRX zeroes out or removes the earnings-announcement window return before ranking, so it does not trade against PEAD. No CZ reversal signal does this. CZ `AnnouncementReturn` is the opposite (drift) signal.
- **Low-volatility half.** The short-horizon report's rank-3 construction is IRRX within low-volatility stocks. CZ publishes no reversal-by-volatility double sort. The exact volatility measure Dai et al. use could not be re-read (paper unreachable), so it is not stated here (UNVERIFIED).

## Disconfirmation
- **Searches run** (WebSearch worked; fetches mostly failed DNS):
  - CZ signal documentation for IndRetBig, STreversal and industry-relative reversal
  - "industry-relative reversal" long leg / short leg after 2000
  - industry-adjusted short-term reversal replication failing after 2010 in large caps, long-only
  - Dai, Medhat, Novy-Marx & Rizova, IRRX long side / short side table
  - Fetches attempted and failed: the Dai et al. PDF (rochester.edu, nber.org), SSRN, IDEAS, the Wharton slides, the Dimensional Q&A, the Alpha Architect and CXO summaries, and the NUS intra-industry reversal paper
- **What was found against:**
  - Search snippets only, unread and so not graded: Stivers & Sun (reversal largely gone since 1990 after seasonality; industry-level reversal negative), and Blitz, van der Grient & Honarvar (2023) testing industry-adjusted reversal, with no post-2010 large-cap figure visible.
  - Also from snippets: a State Street note says reversal "intensified post-2010" (T2, unread, for).
  - None of these gives a long-leg figure.
  - The strongest repo-held evidence against remains Novy-Marx & Velikov's insignificant net 0.19 [1.41] (SH-4) and the plain-reversal proxy's negative long leg (IL-2).

## Caveats & gaps
- **What is missing for rung 2:** the IRRX long leg for 2010-2024 and 2019-2024 (VW and size-screened, with t-statistics), and the same within the low-volatility half. Nothing in this report measures them.
- **Development boundary.** The 2019-2024 and 2010-2024 windows include 2024, after the 2023-12-29 boundary (ADR 0016), as in the 2026-10-09 report.
- **Long leg definition.** Top portfolio minus the mean of the signal's portfolios is a rough long-only proxy, not long minus SPY (as in SH-6).

## What the owner could compute locally
These are research inputs, not a build recommendation.
1. **Proxy check, cheap.** On the CZ release, recompute the `STreversal` long leg for VW deciles and the size-screened file over 2010-2024 and 2019-2024. This only re-checks IL-2 and does not decide rung 2.
2. **IRRX itself.** Build IRRX from firm-level returns: the stock's last-month return minus its industry's (FF industries from SIC), with the earnings-announcement window removed (I/B/E/S or 8-K 2.02 dates), sorted into NYSE-breakpoint quintiles, value-weighted. Do it on all stocks and in the low-volatility half. Report the long leg for both windows.
   - In CRSP this is the faithful version.
   - In our own store it is possible only from about 2019 (bars from ~2019). 2010-2024 is out of reach without CRSP or a history extension.
   - Every run of it is a registered trial.
3. **Data release to record.** Record the CZ release tag shown by `openassetpricing` (`OpenAP().list_release()`, or the release date in the download log). Record the exact command, run as `python -I script.py <data path>` from a script directory separate from the data directory.

## UNVERIFIED items
- IL-2 and every CZ figure: 2026-10-09 team computation, not re-run in this session.
- IL-3 figures: quoted from the 2026-10-09 report. The Dai et al. paper could not be re-opened today.
- The code summaries in IL-1 came through a summarizing fetch tool, not a verbatim read.
- The low-volatility construction details in Dai et al.
- All search-snippet claims in Disconfirmation.

## Follow-up questions (not answered here)
- Does Dai et al.'s published FAJ (2024) version report IRRX portfolio-level (quintile) returns, which would give the long leg directly from the paper?
- Is there another open dataset with industry-relative reversal portfolios (e.g. the JKP factor library or Novy-Marx & Velikov's posted anomaly returns)? Not checked: hosts unreachable.

## Sources
1. Chen, A. Y., & Zimmermann, T. (2022). Open Source Cross-Sectional Asset Pricing. *Critical Finance Review* 11(2). Signal documentation: https://raw.githubusercontent.com/OpenSourceAP/CrossSection/master/SignalDoc.csv ; code: https://raw.githubusercontent.com/OpenSourceAP/CrossSection/master/Signals/pyCode/Predictors/STreversal.py , .../IndRetBig.py (fetched 2026-10-18)
2. Dai, W., Medhat, M., Novy-Marx, R., & Rizova, S. (2023/2024). Reversals and the Returns to Liquidity Provision. NBER WP 30917; *Financial Analysts Journal* 80(2), 122-151. https://mysimon.rochester.edu/novy-marx/research/RRLP.pdf (unreachable today; figures via SH-1/SH-4)
3. Novy-Marx, R., & Velikov, M. (2016). A Taxonomy of Anomalies and Their Trading Costs. *RFS* 29(1). Via SH-2/SH-4.
4. Repo: [short-horizon candidates](2026-10-09-short-horizon-candidates.md) (SH-1, SH-4, SH-6).
