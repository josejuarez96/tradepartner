# Research Report: IRRX long leg, kill-ladder rung 2 (retry)

**Brief:** orchestrator retry of #1734 (prior report: `docs/research/2026-10-18-irrx-long-leg.md`, #1737)  ·  **Date:** 2026-10-18  ·  **Status:** COMPLETE (for the question as framed; see Caveats)  ·  **Agent/model:** research agent, claude-opus-5-5

**Question.** Does IRRX (announcement-adjusted industry-relative one-month reversal; Dai, Medhat, Novy-Marx & Rizova, FAJ 2024) survive ADR 0023 rung 2, the published long leg after 2010?

**Kill criterion (written by the brief before the search).** KILL if the 2010-2024 long-only leg is under 0.10 %/month **or** its t-statistic is under 1.0. If no long leg is published anywhere usable, IRRX is **PARKED behind T198** (not killed).

**Decision it feeds.** Whether IRRX goes to rung 3 or waits for an own-store computation after T198. This report grades evidence. It does not decide.

## Answer
**Verdict:** INSUFFICIENT  ·  **Confidence:** medium (that no usable long leg is published); n/a on the effect itself  ·  **Rung 2 outcome under the brief's rule: PARKED behind T198 (not killed, not passed)**

This retry reached the Dai et al. working paper full text, which the first attempt could not. It confirms, from the paper, that **no long-leg (loser-quintile) return for REV, IRR or IRRX is reported anywhere in it**: Table 2, Table A2 and Figures 1 to 3 and E4 give only winner-minus-loser spreads. The paper reports **no post-2010 subperiod** either. The only post-2001 figure is the long-short spread, 0.58 %/month [t 3.29], May 2001 to December 2021 (Table A2). The open datasets tried (Chen-Zimmermann, JKP, Ken French) have no industry-relative or announcement-adjusted reversal series. Plain reversal is in them, but per Dai et al. the adjustments are exactly what separate IRRX from plain reversal after 2001, so a plain-reversal number cannot decide the rung. **Long-leg number: none published. t: none. Period requested 2010-2024: not covered by any source.**

## Evidence

| Claim | Source | Tier | Key figure (quoted / located) | OOS / post-pub? | Net of costs? |
|---|---|---|---|---|---|
| R2-1 The IRRX paper reports no long leg | Dai et al. (2023 WP, Jan 2023, Rochester PDF), read via a text-extraction proxy (r.jina.ai), all 134,694 characters in two passes | T1 | REV, IRR, IRRX reported only as spreads: Table 2 caption, "REV, IRR, and IRRX buy losers and short winners". No quintile-by-quintile or loser-side table for these strategies. Table 4/E3 give volatility-quintile returns, but for momentum strategies, not reversal | n/a | gross |
| R2-2 No post-2010 subperiod | Same | T1 | The only split is pre/post decimalization. Table A2 covers May 2001 to Dec 2021: IRRX **0.58 [3.29]**, REV 0.18 [0.62], IRR 0.20 [0.94], PEAD 0.36 [2.09], IMOM 0.09 [0.34] (VW, NYSE quintile breaks, long-short). Figure E2 caption: the market-volatility link "persists through the end of our sample, December 2021" | partly (post-2001, in-sample for the paper) | gross |
| R2-3 Full-sample IRRX, for context | Same, Table 2 Panel A, Jan 1973 to Dec 2021 | T1 | IRRX **1.08 [9.35]**, IRR 0.74 [5.40], REV 0.31 [1.68]. Panel B: REV on IRRX, PEAD and IMOM, IRRX loading 0.76 [27.8], adj. R² 87.0 %, residual alpha 0.13 [1.73] | no | gross |
| R2-4 Construction (for any own-store build) | Same, Table 2 and A1 captions, §3.2 | T1 | Prior-month return minus the VW return of the firm's **FF49** industry, minus the **3-day CAR around the most recent earnings announcement**, applied only if the announcement "was in the latest quarter". NYSE quintile breaks, VW, monthly. Table 2 IRRX is the one-month (21-day) lookback; 5-day versions appear in Figures 2 to 4. Volatility = SD of daily returns over the prior 63 days (≥ 42 obs) | — | — |
| R2-5 The FAJ 2024 version has no free supplement | CFA Institute article page (FAJ 80(2), DOI 10.1080/0015198X.2023.2292534) | T1 (publisher page) | Full text and "In Practice" brief members-only. The page links no online supplement. The FAJ text itself was **not read** (UNVERIFIED whether it adds a long-leg table) | — | — |
| R2-6 Chen-Zimmermann has no IRRX-type signal | CZ `SignalDoc.csv` (OpenSourceAP/CrossSection master), both halves read 2026-10-18 | T1 (replication dataset docs) | Reversal rows: `STreversal`, `LRreversal`, `MRreversal`, `MomRev`, `MomOffSeason*`. Industry rows: `IndRetBig`, `ChInvIA` (investment, not returns). **No industry-adjusted return signal and no announcement-adjusted reversal.** Confirms IL-1 | — | — |
| R2-7 JKP: no confirmed IRRX-type characteristic | jkpfactors.com home page; bkelly-lab/ReplicationCrisis and jkp-data READMEs | T1 (dataset docs, partial) | The pages read list no characteristics. The documentation PDF was not reached. From memory only (**UNVERIFIED**): JKP's short-term reversal characteristic is `ret_1_0`, a raw prior-month return with no industry or announcement adjustment, and its public downloads are mainly long-short factors (an April 2026 note mentions download categories "beyond the long-short factor returns", not listed) | — | — |
| R2-8 Proxy only: plain one-month reversal long leg | CZ `STreversal`, size-screened, 2026-10-09 team computation (SH-1 / IL-2), not re-run here | T1 data, own computation | 2010-2024 long leg **−0.16 %/month [t −0.59]**. **This is a PROXY, not IRRX**, and per R2-2 plain REV and IRRX diverge after 2001 (0.18 against 0.58), so it does not decide rung 2 | yes | gross |
| R2-9 Ken French short-term reversal deciles | Ken French data library, ST_Rev 10 portfolios | T1 | **Not computed.** The data are zipped CSVs and this session has no shell. It would be a second plain-reversal proxy (raw prior-month return, no industry or announcement adjustment), so it could not decide the rung even if computed | — | — |

## Disconfirmation
- **Searches and fetches run:**
  - Dai et al. long side / short side (search); the full WP text (two fetch passes covering the whole document); the CFA/FAJ page.
  - JKP characteristic list and leg availability (2 searches, 3 page fetches; raw SAS file 404).
  - "industry-relative short-term reversal failed replication / disappeared after 2010 large caps long-only".
  - Blitz, Hanauer, Honarvar, Huisman & van Vliet, "Beyond Fama-French Factors: Alpha from Short-Term Signals" (industry-relative reversal leg).
  - Blitz, van der Grient & Honarvar (2023), "Reversing the trend of short-term reversal" (Robeco page read; SSRN 4575689 not reached; SSRN 4115411 blocked by a verification page).
  - Novy-Marx & Velikov posted anomaly returns (only the AssayingAnomalies code package found, which needs CRSP).
- **What was found against IRRX:**
  - No source gives a post-2010 IRRX long leg, for or against.
  - Indirect evidence against implementability stays as in the repo: Novy-Marx & Velikov's best net industry-relative reversal, 0.19 [1.41] to 2013 (SH-4, via repo, not re-read).
  - The negative plain-reversal long leg (R2-8, proxy).
  - Robeco's 2023 framing that concern about reversal "vanishing" is widespread (T2, the summary page only; no figures).
- **What was found for IRRX:**
  - Robeco's page says industry- and factor-adjusted ("enhanced") reversal strategies "offer double the risk-adjusted return and remain effective over time". This is T2 practitioner research, long-short, MSCI-type universe, with no long-leg figure on the page read. Not graded as evidence.
  - Blitz et al. (JFE) use industry-relative reversal in MSCI World 1985-2021. The summaries read give no single-signal long-leg figure.

## Caveats & gaps
- **The FAJ 2024 published text was not read** (paywalled). It could contain a decomposition the January 2023 WP does not. This is the one route that could still give a published long leg.
- **The WP was read through a text-extraction proxy and a summarizing model,** not verbatim page renders. Table figures are as the extractor quoted them. Table and figure numbers are from the WP and may differ in the FAJ version.
- **The JKP documentation PDF was not reached.** The "no IRRX-type characteristic" statement for JKP rests partly on memory (UNVERIFIED).
- **Even the paper's long-short evidence stops at December 2021.** Any 2022-2024 figure would need computation.
- **Development boundary:** 2024 lies past the 2023-12-29 boundary (ADR 0016), as noted in the prior report.

## Capacity note at $100 (rung-3 arithmetic, context only; not a rung-3 verdict)
- **Name count.** From the repo's config defaults (`risk.min_order_notional` = 1.0 and `risk.whole_share_price_buffer` = 0.02, `src/tradepartner/config.py` lines 1819-1820), ADR 0023 D6's feasibility rule allows at most about 100 / (1 × 1.02) ≈ **98 names** at $100. A full IRRX loser quintile of a ~1,000-name universe (~200 names) does not fit. A top-N subset (for example 20 to 50 names at $2 to $5 each, fractional notional orders) does.
- **Turnover.** One-month reversal turns over most of the long book each month. Taking the turnover as about 100 % per side per month is an assumption, not measured here.
- **Market impact.** At $100 this is nil. The binding constraints are the order minimum, fractional eligibility and per-fill slippage (rung 1), not capacity.

## UNVERIFIED items
- Everything about the FAJ 2024 text beyond its landing page.
- The JKP characteristic definitions and download contents (R2-7).
- R2-8: the 2026-10-09 computation was not re-run.
- The SH-4 net figures (via repo).
- The Robeco claims (summary pages only).
- The turnover assumption in the capacity note.

## Follow-up questions (not answered here)
- Does the FAJ 2024 published version, or its online supplement on tandfonline, report loser-quintile IRRX returns or a post-2010 split? This needs institutional or CFA-member access.
- Does the JKP documentation list any industry-adjusted short-term return characteristic, and do the "beyond long-short" downloads include tercile leg returns?
- After T198, the own-store IRRX long leg for 2019-2024 (FF49 from SIC, 8-K 2.02 announcement dates). Pre-2019 is out of reach without CRSP (ADR 0022 trigger).

## Sources
1. Dai, W., Medhat, M., Novy-Marx, R., & Rizova, S. (2023). Reversals and the Returns to Liquidity Provision. Working paper, January 2023, https://mysimon.rochester.edu/novy-marx/research/RRLP.pdf (read via https://r.jina.ai/ text extraction, 2026-10-18); NBER WP 30917; *Financial Analysts Journal* 80(2), 122-151 (2024), https://rpc.cfainstitute.org/research/financial-analysts-journal/2024/reversals-and-the-returns-to-liquidity-provision — T1
2. Chen, A. Y., & Zimmermann, T. (2022). Open Source Cross-Sectional Asset Pricing. *Critical Finance Review* 11(2). https://raw.githubusercontent.com/OpenSourceAP/CrossSection/master/SignalDoc.csv — T1
3. Jensen, T. I., Kelly, B., & Pedersen, L. H. (2023). Is There a Replication Crisis in Finance? *Journal of Finance*. https://jkpfactors.com/ ; https://github.com/bkelly-lab/ReplicationCrisis ; https://github.com/bkelly-lab/jkp-data — T1 (docs, partial)
4. Blitz, D., van der Grient, B., & Honarvar, I. (2023). Reversing the trend of short-term reversal. Robeco summary: https://www.robeco.com/en-uk/insights/2023/10/reversing-the-trend-of-short-term-reversal (SSRN 4575689, not read) — T2
5. Blitz, D., Hanauer, M. X., Honarvar, I., Huisman, R., & van Vliet, P. Beyond Fama-French Factors: Alpha from Short-Term Signals. *JFE*; summaries: https://alphaarchitect.com/alpha-from-short-term-signals/ , https://www.robeco.com/nl-be/visie/2022/05/beyond-fama-french-alpha-from-short-term-signals — T2 (summaries)
6. Repo: `docs/research/2026-10-18-irrx-long-leg.md` (IL-1, IL-2), `docs/research/2026-10-09-short-horizon-candidates.md` (SH-1, SH-4), `docs/decisions/0023-break-first-fast-books-paper-exam.md` (rungs, D6), `src/tradepartner/config.py` (risk defaults).
