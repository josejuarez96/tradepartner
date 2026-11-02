# Research Report: Strategy first, data second. Which published US-equity predictors survive publication, liquid names and our cost?

**Brief:** orchestrator brief of 2026-10-30 (the owner's question of the same day, "are we picking strategies that fit our data store instead of finding the strategies with the best evidence?"; no issue number)  ·  **Date:** 2026-10-30  ·  **Status:** INCOMPLETE. Per-signal post-publication returns and t-statistics from the Chen-Zimmermann portfolio files were **not computed** (see "Why INCOMPLETE"). The ranking falls back to meta-studies and to per-signal figures the repo already computed, as the brief allows.  ·  **Agent/model:** research subagent, claude-opus-5-5. No shell, no store read, no `.env`, no git. 21 web calls (11 searches, 10 fetches), against a self-set cap of 25.

## Why INCOMPLETE (read this first)
- **What the brief asked for:** for each Chen-Zimmermann (CZ) predictor, the post-publication long-short and long-leg return with its t-statistic, value-weighted (VW) or size-screened (the files the [B9 note](2026-10-18-b9-kill-b3-retirement.md) used).
- **What blocked it:**
  - The files exist on the CZ Hugging Face release (2026.10). Their paths are `Portfolios/Full Sets Alt/PredictorAltPorts_DecilesVW.parquet` and `.../PredictorAltPorts_LiqScreen_ME_gt_NYSE20pct.parquet`, listed through the Hub API on 2026-10-30.
  - They are parquet (binary). This session had no shell to read them. The web fetch tool returns text only, and the dataset viewer API returns rows, not averages. The 10-09 team computed its CZ figures with the `openassetpricing` package in a shell.
  - The PDFs of Chen-Welch (2026) and Jensen-Kelly-Pedersen (2021 NBER) came back compressed and unreadable. The arXiv HTML of Chen-Welch carries the text but not its tables.
- **What this report used instead:**
  - (a) The category-level meta-studies the brief names, with Chen-Welch (2026) as the closest match to the brief's question.
  - (b) Per-signal CZ post-publication figures the repo already holds (10-09 [short-horizon report](2026-10-09-short-horizon-candidates.md), team computation).
  - (c) In-sample VW figures from the CZ catalogue (`SignalDoc.csv`, read locally).
- **Consequence:** most return cells below are ranges, not one post-publication number with a t-statistic. Each range is marked with where it comes from.

## Answer
**Verdict:** On the narrow question (did the data-first queue exclude the better-evidenced strategies?): **SUPPORTED**. On the bigger question (does any published anomaly clearly survive our cost in liquid names?): **MIXED**.  ·  **Confidence:** medium on the queue audit (from reading its code and outputs); low on remaining uncomputed per-signal magnitudes; CZ rows 4, 7 and 8 were computed locally in this PR.

1. **Yes, the queue picked by data first, and it cut out most of the best evidence.**
   - Chen and Welch (2026) name the predictors that did best in large and mid caps after 2005: cash-based operating profitability, R&D-adjusted operating profitability, gross profitability, net external financing, net equity financing, off-season and long-horizon seasonal momentum, and the realized-minus-implied volatility spread.
   - The queue's script dropped almost all of them before it looked at any evidence:
     - all profitability and momentum signals, under a rule called "plain factor, already tested and lost". Only gross profitability was ever tested (B3);
     - the options signal, as not held;
     - the two financing signals, as near-duplicates of a weaker one.
   - Of the 15 below, the queue excluded or mis-ranked **12**. One (asset growth) it ranked fairly. Two are not in the catalogue at all.
2. **Strategy first does not reveal a big hidden winner.**
   - The best post-2005 large and mid-cap long-short spreads are about **0.6% a month gross for the very best** and about **0.25% a month on average for profitability**.
   - After Chen-Welch's own correction for luck, the very best is about **0.06% a month**.
   - The median anomaly in that universe after 2005 earns **0.07% a month** before costs.
3. **Our cost kills fast signals, not slow ones.**
   - At 24.2 bp a side, a signal that re-sorts once a year (about 10% of the book a month) costs about 0.10% a month long-short, or 0.05% for one leg.
   - Any signal that turns over half the book or more each month needs about 1% a month of gross long-short edge. None in the post-2005 large-cap evidence has that.
   - So the slow families (profitability, financing, investment, off-season momentum) pass the cost rule if the raw spreads are believed, and fail it if the luck-adjusted ones are.
4. **The data for the slow survivors is mostly free.**
   - Gross profitability, asset growth and share issuance are held.
   - Cash-based profitability, the two financing signals and R&D-adjusted profitability are **parse-only**: their XBRL tags are in the cached `companyfacts.zip` but not parsed.
   - Off-season momentum needs five years of returns. Our bars give that from 2021 on; a longer backtest needs pre-2016 prices (Sharadar Prices, Full History, USD 299 a year).
   - Only the options signal needs expensive data (implied volatility; vendor price UNVERIFIED).

**CZ return-cell update (2026-10-30):** Rows 4, 7 and 8 use the local [compute.py](/Users/josejuarez/tradepartner-probes/issuance/compute.py) [output](/Users/josejuarez/tradepartner-probes/issuance/out.txt), in percent per month, for 2009-01 through 2025-12. Other rows and estimated net cells remain as originally researched; their cost and turnover assumptions are not settled by these files. The overall report remains incomplete because most per-signal cells are still uncomputed.

## 1. Top 15, strategy first

**How to read the table.**
- **Ranking.** Ranked by the lower end of the post-2005 or post-publication large-cap long-short edge after our cost, then by how many independent sources back the edge. Ranks 1 to 6 cannot really be separated with the evidence in hand.
- **Edge.** Long-short, % a month, gross, before borrow. "CW" is Chen-Welch (2026), non-micro (top 3,000 names that are also in the top 90% of market value), January 2006 onward.
  - "CW best 0.59-0.66" is their range for the best strategies, which they name in the text. The **per-signal CW figure is UNVERIFIED** because their tables did not render.
  - "CW floor 0.07" is their median anomaly, used as the low end where nothing better exists.
  - "CW luck-adj 0.06" is their luck-adjusted figure for the best strategy.
- **Turnover.** One-sided, per leg, per month. **All turnovers are estimates** from CZ's rebalancing period (`Portfolio Period`, `SignalDoc.csv`) and from published turnovers where cited. Annual-rebalance signals use 10% (B3's measured 10.4% on a similar construction).
- **Cost.** Rung-1 rule: kill if 2 × turnover × cost > monthly edge, applied to each leg. So long-short cost = 4 × turnover × cost per side, and long-only cost = half of that.
  - At 24.2 bp: 10% turnover costs 0.10 long-short (0.05 long-only); 34.5% costs 0.33 (0.17); 90% costs 0.87 (0.44); 100% costs 0.97 (0.48).
  - At 15.6 bp: 0.06 (0.03), 0.22 (0.11), 0.56 (0.28), 0.62 (0.31).
  - The 15.6 bp figure is the paper **buy** cost in names over $200M of daily dollar volume, used here as the brief asks. The per-side figure in those names is 10.6 bp ([cost report](2026-10-30-paper-fill-cost-arrival.md)).
- **Data status:**
  - **held**: in the store now;
  - **parse-only**: the us-gaap tags are in the cached `companyfacts.zip` but not parsed. The store has 8 line items: revenue, gross_profit, cost_of_revenue, total_assets, operating_cash_flow and three bank items ([10-28 census](2026-10-28-strategy-frontier-verified.md));
  - **free to add**: public source, not ingested;
  - **paid**: a vendor is needed.

| # | Signal (paper) | Plain description | Post-pub / post-2005 large-cap LS edge, t | Est. turnover | Net at 24.2 bp | Net at 15.6 bp | Data needed | Status | Grade |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `CBOperProf` (Ball et al. 2016) | Buy firms with high cash profits (operating profit with accruals removed) relative to assets | CW best list; profitability category avg ~0.25; CW best 0.59-0.66; luck-adj 0.06. In-sample VW decile 0.47, t 3.17 (1963-2014, catalogue). t post-2005: UNVERIFIED | 10% (annual) | +0.15 to +0.56 (luck-adj -0.04) | +0.19 to +0.60 (luck-adj 0.00) | Revenue, COGS, SG&A, R&D, changes in receivables, inventory, prepaids, deferred revenue, payables, accrued liabilities; total assets | parse-only (revenue, COGS, assets held) | MIXED |
| 2 | `GP` (Novy-Marx 2013) | Buy firms with high gross profit / assets | CW best list; in-sample VW quintile 0.31, t 2.49 (1963-2010); 0.31 × 0.42 (McLean-Pontiff cut) = 0.13; CW best 0.59-0.66. **Own store:** B3 trial 3, 2020-08 to 2023-12, long tilt, net -3.28 pp/yr vs SPY | 10% (B3 measured 10.4%) | +0.03 to +0.56 | +0.07 to +0.60 | Revenue, COGS, total assets | held | MIXED |
| 3 | `XFIN` (Bradshaw, Richardson, Sloan 2006) | Avoid (short) firms raising net outside money; buy firms paying it out | CW best list (truly post-publication: sample ends 2000); floor 0.07, best 0.59-0.66. In-sample EW 1.29, t 5.70 | 10% | -0.03 to +0.56 | +0.01 to +0.60 | Stock issued, stock bought back, dividends paid, long-term debt issued and repaid (cash-flow tags); assets | parse-only | MIXED |
| 4 | `NetEquityFinance` (same paper) | The equity-only half of #3 | CW best list; **CZ 2009–2025 VW L/S +0.147 (t 0.42), long +0.156 (t 1.06); size-screened L/S +0.315 (t 1.21), long +0.083 (t 0.82)**. In-sample EW 0.93, t 3.82 | 10% | -0.03 to +0.56 | +0.01 to +0.60 | Stock issued and bought back; assets | parse-only | MIXED |
| 5 | `OperProfRD` (Ball et al. 2016) | Operating profit with R&D added back, over assets | CW best list; in-sample VW 0.29, t 1.84 (CZ: "2_likely", the weaker tier); profitability range as #1 | 10% | +0.15 to +0.56 | +0.19 to +0.60 | Revenue, COGS, SG&A, R&D, assets | parse-only | MIXED |
| 6 | `MomOffSeason` (Heston, Sadka 2008) | Buy stocks whose average return in the *other* calendar months of the past 2-5 years was low (an off-season reversal) | CW best list; floor 0.07, best 0.59-0.66. In-sample EW 1.25, t 5.6 (NYSE/AMEX, 1965-2002) | 15% (estimate: one month enters and one leaves a 44-month average) | -0.08 to +0.51 | -0.02 to +0.57 | Monthly returns, years 2-5 back | held from 2021-01 (bars start 2016-01-04); a backtest before that needs pre-2016 prices: **paid**, Sharadar Prices Full History USD 39/mo or 299/yr, 1998 onward ([pre-2016 report](2026-10-27-pre-2016-price-history.md) E2) | MIXED |
| 7 | `AssetGrowth` (Cooper, Gulen, Schill 2008) | Buy slow asset growers, avoid fast ones | Not on the CW best list. **CZ 2009–2025 VW L/S −0.172 (t −0.61), long −0.116 (t −0.57); size-screened L/S +0.458 (t 2.27), long +0.163 (t 1.12)**. In-sample VW 0.70 (1968–2003); × 0.42 prior = 0.29 | 10% | -0.03 to +0.19 | +0.01 to +0.23 | Total assets | held | MIXED (QI-15) |
| 8 | `ShareIss1Y` (Pontiff, Woodgate 2008) | Buy net repurchasers, avoid net issuers | **CZ 2009–2025 VW L/S +0.671 (t 2.48), long +0.162 (t 1.44); size-screened L/S +0.350 (t 1.68), long +0.134 (t 1.61). Q1 declared 2010–2019 screened long +0.0996 (t 1.12) kills Q1.** Short leg survives a low-fee screen (-0.25, -0.17 net of borrow, 2006-2020, LS-20); long leg weaker after 2002 ([Q1 note](2026-10-19-q1-repurchasers-rung12.md)) | 10% | UNVERIFIED (break-even 0.10) | UNVERIFIED (break-even 0.06) | Split-adjusted share count | held (cover-page shares) | MIXED (QI-15) |
| 9 | `Mom12m` (Jegadeesh, Titman 1993) | Buy last year's winners, skip the last month | CW text: "about 50 bps per month in the top 90%" of stocks, but momentum "essentially dead" under their Standard filter (the two statements conflict as read); Novy-Marx-Velikov VW net 0.68 to 2012 (G1 report) | 34.5% (Novy-Marx-Velikov) | -0.33 to +0.17 | -0.22 to +0.28 | Prices | held | MIXED (G1-1) |
| 10 | `AnnouncementReturn` (Chan, Jegadeesh, Lakonishok 1996) | Buy stocks with the best 4-day reaction to earnings | **Repo CZ computation:** VW post-pub (1997-2024) 0.99, t 4.47; 2010-2024 0.81, t 3.12; size-screened 2010-2024 0.25, t 2.15; long leg VW 0.32 (t 1.88), size-screened 0.07 (t 0.94) | 100-200% (B9 file) | VW 2010-24: -0.16; size-screened: -0.72 | VW: +0.19; size-screened: -0.37 | Earnings release time (8-K Item 2.02) plus bars | free to add (EDGAR `items`, T164e); bars held | MIXED (SH-6); B9 killed |
| 11 | `RevenueSurprise` (Jegadeesh, Livnat 2006) | Buy unexpectedly strong revenue growth | Repo CZ: VW post-pub (2007-2024) 0.34, t 1.68; 2019-2023 -0.29; size-screened 2010-24 0.22, t 1.54 | 50% (estimate: quarterly signal, monthly hold) | -0.14 | +0.03 | Revenue per share; release time | held, but the store's time is the later 10-Q acceptance; 8-K 2.02 free to add | MIXED (SH-7) |
| 12 | `RIVolSpread` (Bali, Hovakimian 2009) | Avoid stocks whose realized volatility is above their option-implied volatility | CW best list; floor 0.07, best 0.59-0.66. In-sample VW 0.73, t 2.9 (1996-2004) | 70% (estimate, monthly) | -0.61 to -0.02 | -0.37 to +0.22 | ATM implied volatility | **paid**: OptionMetrics (institutional, via WRDS) or a retail options-data vendor; **price UNVERIFIED** (not searched) | INSUFFICIENT (one post-pub source) |
| 13 | IRRX (Dai, Medhat, Novy-Marx, Rizova 2024; not in CZ) | Buy last month's losers relative to their industry, after removing earnings-day moves | VW LS 0.58, t 3.29, May 2001 to Dec 2021 (mostly before publication) | 90% | -0.29 | +0.02 | 8-K dates, SIC | free to add / held | MIXED; parked |
| 14 | `MomSeason11YrPlus` / `16YrPlus` (Heston, Sadka 2008) | Buy stocks that did well in this same calendar month 11-20 years ago | CW best list ("long-horizon seasonal momentum"; which variant is UNVERIFIED); floor 0.07, best 0.59-0.66. In-sample EW 0.66 (t 6.4) / 0.52 (t 4.6) | 90% (estimate: the ranking month changes every month) | -0.80 to -0.21 | -0.49 to +0.10 | 11-20 years of monthly returns | **paid** (Sharadar from 1998 gives the 16-20 year version only from about 2018); not computable from our bars until 2027+ | INSUFFICIENT |
| 15 | Short-term momentum (Medhat, Schmeling 2022; not in CZ) | Buy last month's winners among the most-traded names | Largest-500 LS 0.42, t 4.91 (1963-2018, in-sample); no post-2018 US test found (SH-9) | 90% (paper) | -0.45 | -0.14 | Prices, volume, shares | held (B10) | INSUFFICIENT |

**What the table says in plain words.**
- **Every signal that trades more than about a third of its book a month fails at 24.2 bp.** That covers ranks 10 to 15, plus momentum at the low end of its range. At 15.6 bp, only the value-weighted earnings-reaction spread (rank 10) and, at the top of its range, the options signal clear the line.
- **Slow signals have mixed measured spreads.** CZ VW AssetGrowth is negative, VW NetEquityFinance is only +0.147, and VW ShareIss1Y is +0.671%/mo in 2009+. The other slow rows retain estimates and literature ranges. Turnover and the gross edge both matter; Chen-Welch's luck-adjusted 0.06%/mo remains below the estimated cost.
- **Long-only halves the cost but also gives up the short leg.** LS-6 (SUPPORTED) puts most anomaly profit in the short leg. CZ long-leg figures are now available for ranks 4, 7 and 8 above; ranks 1–3, 5–6 and 9 remain uncomputed here.

## 2. What the data-first queue excluded or mis-ranked

Source: `~/tradepartner-probes/idea-queue/process_signals.py` (rules), `first-pass.md`, `candidates.csv`, `top20.md`.

| Fate in the queue | Count | Signals (rank above) | Why |
|---|---|---|---|
| Dropped before ranking: "plain momentum/size/value/profitability factor (already tested and lost)" | **6** | CBOperProf (1), GP (2), OperProfRD (5), MomOffSeason (6), Mom12m (9), MomSeason long-horizon (14) | `is_plain_factor()` drops every acronym containing "mom", every "off season" or "return seasonality" description, and every profitability signal. Only GP was ever tested (B3, retired). Momentum is MIXED (G1-1), not lost. CBOperProf, OperProfRD and the seasonal signals were never tested. |
| Dropped as NOT_AVAILABLE | **2** | RIVolSpread (12); AnnouncementReturn (10) | Options keyword. AnnouncementReturn is absent from the kept list and is not a "plain factor", so it fell to the NOT_AVAILABLE rule (the exact keyword is UNVERIFIED: likely "daily return" or "ibes"). Its data is free (8-K 2.02). |
| Kept, then cut as a "near-duplicate" in the top 20 | **4** | XFIN (3), NetEquityFinance (4), ShareIss1Y (8), RevenueSurprise (11) | Clustering kept CompositeDebtIssuance (in-sample t 8.59) over XFIN and NetEquityFinance, the two that Chen-Welch name as post-2005 survivors. The tie-break was in-sample t, not post-publication evidence. |
| Ranked in the top 20 | 1 | AssetGrowth (7; queue rank 7) | Fair. |
| Not in the CZ catalogue | 2 | IRRX (13), short-term momentum (15) | Out of the queue's reach by construction. |

**12 of 15** were excluded or mis-ranked. Four further errors in the queue are worth stating:
- **Its data labels were wrong both ways.** It marked all accounting signals HELD, though only 8 line items are parsed. It marked the free 8-K-based AnnouncementReturn NOT_AVAILABLE.
- **Its top 3 includes two signals the repo had already found dead after publication.**
  - DivSeason (its #1): CZ post-publication 0.01, t 0.21 (SH-10).
  - EarningsSurprise (its #2): CZ VW post-publication -0.06, t -0.52 (SH-5).
- **It preferred small and micro caps, where the published returns sit.** Chen-Welch's 26 bp (non-micro, all years) against 7 bp (non-micro, post-2005) shows that the micro tilt and the old samples carry most of the in-sample numbers.
- **It ranked by the paper's own t-statistic.** McLean-Pontiff find larger post-publication decay for signals with higher in-sample returns and t-statistics, so this ordering points the wrong way.

## 3. Category view: which families survive after publication in large caps at all

Grades follow handoff §6.1, for the claim "this family has a positive post-publication (or post-2005) gross edge in liquid US names that clears our cost".

| Family | Large-cap post-pub evidence | Against | Grade |
|---|---|---|---|
| Profitability (gross, operating, cash-based) | CW: named among the best non-micro post-2005; category average about 25 bp a month (T1 WP). JKP: published factors replicate at about 82% (E3). Novy-Marx-Velikov: low-turnover anomalies keep net spreads (T1). Repo QI-6 SUPPORTED for slow, liquid profitability tilts | CW luck-adj best 6 bp. Own B3 trial: -3.28 pp/yr vs SPY over 40 months (low power, long tilt). The CW post-2005 window is partly in-sample for papers with samples to 2010 or 2014 | **MIXED** |
| External financing / issuance (XFIN, net equity, share issuance) | CW names XFIN and NetEquityFinance (sample ended 2000, so post-2005 is out of sample). LS-20: the issuance short leg survives a low-fee screen | QI-15 MIXED (redundancy with investment); long leg weaker after 2002 (Q1 note); short leg needs borrow | **MIXED** |
| Investment (asset growth, accruals, NOA) | VW asset-growth spread positive in-sample (0.70) | Not on the CW best list; frontier prior about 0.07; HXZ: 65% of anomalies fail with NYSE breakpoints and VW | **MIXED** |
| Momentum, 12-1 | Novy-Marx-Velikov VW net 0.68 to 2012 | CW: momentum, value and intangibles "essentially dead" under the Standard filter; G1-1 MIXED (live long-only products near zero net, 2009-2025) | **MIXED** |
| Seasonality (off-season reversal, long-horizon same-month momentum) | CW names both as survivors; Keloharju-Linnainmaa-Nyberg (JFE 2021) find large seasonal reversals | JKP list seasonality among the three themes below 75% replication (secondary summary); long-horizon versions turn over almost fully each month | **MIXED** for off-season (slow); **INSUFFICIENT** for long-horizon same-month (one post-pub source, and it fails cost) |
| Value | none retrieved post-2005 large-cap | CW "essentially dead"; QI-14 MIXED | **MIXED** (leaning against) |
| Earnings-event drift | CZ AnnouncementReturn VW post-pub 0.99 (t 4.47), 0.81 in 2010-2024 | Size-screened 0.25; long leg 0.07 to 0.32; SUE-PEAD NOT SUPPORTED (SH-5); turnover 100%+ | **MIXED**; fails our cost |
| Short-horizon reversal, industry momentum | none | SH-1, SH-3: CZ VW post-pub -0.05 and 0.06; Novy-Marx-Velikov nets negative | **NOT SUPPORTED** |
| Dividend seasonality | none | CZ post-pub 0.01 (t 0.21) | **NOT SUPPORTED** |
| Trading frictions / liquidity / price delay | none in liquid names | HXZ: 96% of the frictions group fails; the premium sits in the smallest names | **NOT SUPPORTED** in liquid names |
| Options-implied (RIVolSpread and kin) | CW names RIVolSpread | one source; paid data; high turnover | **INSUFFICIENT** |
| Intangibles | none | CW "essentially dead" (one source) | **INSUFFICIENT** |
| Short-term momentum (high-turnover names) | Medhat-Schmeling largest-500 in-sample | no post-2018 test; 90% turnover | **INSUFFICIENT** |

## 4. Disconfirmation: the case that NO published anomaly survives our cost in liquid names

**Searches run (2026-10-30):**
1. Chen-Zimmermann files and summaries (site, Hugging Face listing, Results tables, signal browser).
2. Chen-Welch "What Useful Alphas?" (abstract, HTML, PDF, one search).
3. Jensen-Kelly-Pedersen size groups (two searches, NBER PDF, Alpha Architect summary: 403).
4. Chen-Velikov survivors under value-weighting.
5. Seasonality post-publication.
6. McLean-Pontiff by predictor type.
7. Gross-profitability post-publication in large caps.
8. Anomalies disappearing in large caps, long-only, net of costs.

**The case against, at its strongest:**
- **Chen and Welch (2026, T1 WP, abstract and §II-III, VI):**
  - Median non-micro post-2005 anomaly: about **7 bp a month** gross. Median t **0.45**. Var(t) **1.09**, "almost exactly what luck alone would have produced". The signal share is about 0.08.
  - The best survivor's raw **66 bp** is "only 6 bps" after luck adjustment.
  - Their conclusion: published anomalies were of little use to non-micro managers in the 21st century.
  - Their non-micro universe (top 3,000 within the top 90% of market value) is close to our top-1,000 universe, though a little broader than it.
- **Chen and Velikov (JFQA 2023, T1, abstract and search summaries):**
  - The average anomaly earns **4 bp a month** net of costs and post-publication effects.
  - The strongest net **at best 10 bp**. Value-weighting implies "11 bps per month, at best" (FEDS draft).
  - No sort yields a reliable pattern in post-publication, post-2005 net returns.
- **McLean and Pontiff (JF 2016, T1):**
  - Returns are 26% lower out of sample and 58% lower after publication.
  - Post-publication returns are lower for signals concentrated in liquid, cheap-to-arbitrage stocks: our universe.
- **Short legs carry the profit (LS-6, SUPPORTED, JF 2025):** anomaly long-short returns are about zero net of borrow. A long-only book keeps the weaker half.
- **Our own store (B3):** the most-cited slow survivor, gross profitability, lost 3.28 pp a year to SPY net over 40 months in our universe. Low power, but it is the only post-2020 test on our data.
- **Arithmetic.** If the luck-adjusted 6 bp is the true gross edge, even a 10%-turnover signal at our 24.2 bp (cost 5 bp long-only, 10 bp long-short) nets about zero or less. Under that reading **no** signal in the table survives.

**What was found against the disconfirmation:**
- Chen-Welch's own raw best of 59-66 bp, and a profitability average of about 25 bp.
- Chen-Zimmermann (2017 draft, via CXO): publication bias is only about 13% of returns, so most of the in-sample spread was real; about 52% of the gross return persists post-publication on average (0.37% a month, equal-weighted, concentrated in volatile, costly stocks).
  - Note: the CXO summary words this as "52% of gross profitability". As read, it means the average anomaly's gross profit, not the GP signal. UNVERIFIED against the paper.
- Jensen-Kelly-Pedersen: about 82% of factors replicate, out of sample internationally.
- Frazzini-Israel-Moskowitz (via search summary): size, value and momentum survive real institutional trading costs.
- Novy-Marx-Velikov: anomalies under 50% one-sided turnover keep significant net spreads with buffers.

**Net reading:** The disconfirming case is strong for fast signals and for the median anomaly. For the handful of slow profitability, financing and off-season signals, it turns on which post-2005 number is believed: the raw 0.25-0.66, or the luck-adjusted 0.06. Neither side's evidence settles that.

## Evidence

| Claim | Source | Tier | Key figure (quoted) | OOS / post-pub? | Net of costs? |
|---|---|---|---|---|---|
| Median non-micro post-2005 anomaly about 7 bp/mo; best survivors named; best raw 59-66 bp; luck-adj 6 bp | Chen, Welch, "What Useful Alphas?", arXiv 2607.06502 (2026), abstract, §II, §III, §VI | T1 (WP, unrefereed) | "raw returns of the best strategies of about 59 to 66 bps per month"; "a raw return of 66 bps per month, had a luck-adjusted return of only 6 bps"; profitability category "about 25 bps per month" | yes (post-2005) | no (gross) |
| CW Standard filter = top 3,000 ∩ top 90% of market value | same, §II | T1 | "the intersection — top 3,000 stocks that also fall within the top 90% of market cap" | n/a | n/a |
| Average anomaly 4 bp/mo net; VW at best 11 bp | Chen, Velikov, JFQA 2023; FEDS 2020-039 | T1 | "a measly 4 bps per month"; "11 bps per month, at best" | yes | yes |
| Decay 26% OOS, 58% post-pub; larger in liquid stocks | McLean, Pontiff, JF 2016 (via search summaries) | T1 | "26% lower out-of-sample and 58% lower post-publication" | yes | no |
| Publication bias about 13%; about 52% of gross return persists post-pub | Chen, Zimmermann 2017 draft, via CXO Advisory | T1 via T2 summary | "on average 13% smaller"; 0.37%/mo | yes | no |
| Per-signal CZ post-pub figures (AnnouncementReturn, RevenueSurprise, STreversal, IndMom, EarningsSurprise, DivSeason) | repo [short-horizon report](2026-10-09-short-horizon-candidates.md) SH-1 to SH-10, team computation 2026-10-09 | T1 data, own computation (unchecked) | e.g. AnnouncementReturn VW 0.99 [4.47] post-1996 | yes | no |
| In-sample VW figures for GP, CBOperProf, OperProfRD, RIVolSpread | CZ `SignalDoc.csv` (`Return`, `T-Stat`, `Stock Weight`) | T1 (replication catalogue) | GP 0.31 t 2.49 VW; CBOperProf 0.47 t 3.17 VW; OperProfRD 0.29 t 1.84 VW; RIVolSpread 0.732 t 2.9 VW | no | no |
| Momentum and PEAD turnover; low-turnover strategies survive | Novy-Marx, Velikov, RFS 2016, via repo G1 and SH-6 | T1 | 12-1 34.5%/mo; PEAD 34.7%/mo, net 0.34 | partly | yes |
| Our cost | repo [cost report](2026-10-30-paper-fill-cost-arrival.md) | own result (paper fills) | +24.2 bp per side; buys over $200M +15.6 | paper only | n/a |
| Queue rules | `process_signals.py`, `first-pass.md`, `top20.md` | own code read | 40 dropped as "plain factor", 55 NOT_AVAILABLE | n/a | n/a |
| CZ files exist and their names | Hugging Face API tree, OpenSourceAP/CrossSection (release 2026.10) | T1 (dataset) | `PredictorAltPorts_DecilesVW.parquet`, `PredictorAltPorts_LiqScreen_ME_gt_NYSE20pct.parquet` | n/a | n/a |
| Pre-2016 prices: Sharadar USD 39/mo, 299/yr, from 1998 | repo [pre-2016 report](2026-10-27-pre-2016-price-history.md) E2 | T1 (vendor page, read 2026-10-27) | "Full History … $39 … $299" | n/a | n/a |

## Caveats & gaps
- **The biggest one.** Chen-Welch's "post-2005" is not "post-publication" for papers whose samples run past 2005. GP's sample ends in 2010, CBOperProf's and OperProfRD's in 2014. So part of their post-2005 return is in-sample, which flatters the profitability rows. XFIN, NetEquityFinance and the Heston-Sadka signals (samples end 2000-2002) are genuinely post-publication there.
- Per-signal CW numbers did not render. The ranges in the table are the paper's range for its best strategies applied to each named one, not each one's own figure.
- CW's own text on momentum conflicts as read (about 50 bp in the top 90%, yet "essentially dead" under the Standard filter); the fetch summary may have run two statements together.
- Turnover is estimated for every row except momentum, PEAD and short-term momentum. Long-short cost assumes both legs trade at our per-side cost and leaves out borrow fees.
- The 24.2 and 15.6 bp figures are paper fills over three sessions, mostly tiny orders (cost report caveats).
- The repo's CZ computations (rows 10, 11) were never independently checked.
- "parse-only" assumes the needed us-gaap tags exist in `companyfacts.zip` for most filers. Tag coverage (e.g. R&D, deferred revenue, debt issuance) was not measured. XBRL starts in 2009.

## UNVERIFIED items
- Each CW-named signal's own post-2005 return and t.
- Which long-horizon seasonal variant CW mean.
- Every turnover estimate marked "estimate".
- The keyword that dropped AnnouncementReturn as NOT_AVAILABLE.
- Options-data vendor prices (not searched).
- JKP's seasonality replication figure (secondary summary only).
- The CZ 2017 "52%" reading.
- Frazzini-Israel-Moskowitz figures (search summary only).
- Long-leg-only post-2005 large-cap returns for ranks 1–3, 5–6 and 9 remain uncomputed here; CZ rows 4, 7 and 8 are shown above.

## Follow-up questions (not answered here)
1. **The computation this report could not run.** With a shell, read `PredictorAltPorts_DecilesVW` and `PredictorAltPorts_LiqScreen_ME_gt_NYSE20pct`. For every predictor, compute long-short and long-leg means and t-statistics after `SampleEndYear` and after the publication `Year`, and over 2010-2024. Use the B9 conventions and the repo's team computation. This would replace every range in section 1 with a number, and it costs no data.
2. What share of filers carry the cash-flow and expense tags that ranks 1, 3, 4 and 5 need, in the cached `companyfacts.zip`?
3. What is the borrow cost for the short legs of ranks 1 to 8 in our universe (LS-6 says it decides long-short profitability)?
4. Is Chen-Welch's luck adjustment (1 − 1/Var(t), pooled across all anomalies) the right prior for a pre-selected family, or does it over-shrink the strongest few? This is a methods question for the research program, not answered here.

## Sources
- Chen, A. Y., and Welch, I. (2026). "What Useful Alphas?" arXiv 2607.06502: [abstract](https://arxiv.org/abs/2607.06502), [HTML](https://arxiv.org/html/2607.06502), [IDEAS](https://ideas.repec.org/p/arx/papers/2607.06502.html). T1 (WP).
- Chen, A. Y., and Velikov, M. (2023). "Zeroing in on the Expected Returns of Anomalies", JFQA: [Cambridge](https://www.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/zeroing-in-on-the-expected-returns-of-anomalies/945133D5A3ECEEAF466AEE91551FD225); [FEDS 2020-039](https://www.federalreserve.gov/econres/feds/files/2020039pap.pdf). T1.
- McLean, R. D., and Pontiff, J. (2016). "Does Academic Research Destroy Stock Return Predictability?" JF 71(1): [summary](https://law-journals-books.vlex.com/vid/does-academic-research-destroy-855658380). T1 via summary.
- Chen, A. Y., and Zimmermann, T. "Publication Bias and the Cross-Section of Stock Returns" (2017 draft), via [CXO Advisory](https://cxoadvisory.com/?p=30583). T1 via T2.
- Chen, A. Y., and Zimmermann, T. Open Source Asset Pricing: [site](https://www.openassetpricing.com/), [Hugging Face dataset](https://huggingface.co/datasets/OpenSourceAP/CrossSection), [file tree API](https://huggingface.co/api/datasets/OpenSourceAP/CrossSection/tree/main?recursive=true), [Results/bigSignalTableClear.tex](https://huggingface.co/datasets/OpenSourceAP/CrossSection/resolve/main/Results/bigSignalTableClear.tex); local `~/tradepartner-probes/idea-queue/raw/SignalDoc.csv`. T1.
- Jensen, T. I., Kelly, B., and Pedersen, L. H. (2023). "Is There a Replication Crisis in Finance?" JF: [NBER w28432](https://www.nber.org/papers/w28432) (PDF unreadable here). T1, summary level only.
- Keloharju, Linnainmaa, Nyberg, "Are return seasonalities due to risk or mispricing?" JFE 2021: [RePEc](https://econpapers.repec.org/RePEc:eee:jfinec:v:139:y:2021:i:1:p:138-161); Heston-Sadka replication note via [Nasdaq PDF](https://www.nasdaq.com/docs/Seasonal%20Reversals%20in%20Expected%20Stock%20Returns.pdf). T1 via search summary.
- Frazzini, Israel, Moskowitz, via [Alpha Architect](https://alphaarchitect.com/surprise-the-size-value-and-momentum-anomalies-survive-after-trading-costs/); Novy-Marx and Velikov, [NBER w20721](https://www.nber.org/papers/w20721.pdf). T1/T2 via summary.
- Repo: [short-horizon candidates](2026-10-09-short-horizon-candidates.md), [B9 kill and B3 retirement](2026-10-18-b9-kill-b3-retirement.md), [strategy frontier verified](2026-10-28-strategy-frontier-verified.md), [paper fill cost vs arrival](2026-10-30-paper-fill-cost-arrival.md), [pre-2016 price history](2026-10-27-pre-2016-price-history.md), [Q1 repurchasers](2026-10-19-q1-repurchasers-rung12.md), [beyond long-only](2026-10-10-beyond-long-only.md), [claims.toml](claims.toml) (QI-2, QI-6, QI-14, QI-15, G1-1, G4-4, G4-7, LS-6, LS-20, SH-*); queue files in `~/tradepartner-probes/idea-queue/`.
