# Research Report: I1 asset growth, rung-2 desk read against a cost-fixed kill line

**Brief:** orchestrator brief of 2026-10-30 (owner decision on #836: run the I1 desk read now, kill line fixed at the measured cost before reading the evidence)  ·  **Date:** 2026-10-30  ·  **Status:** COMPLETE (declared CZ AssetGrowth read computed from local parquets)  ·  **Agent/model:** research subagent, claude-opus-5-5. No shell (so no `git fetch`; files read from the working tree of the main checkout), no store reads, no backtests.

## Step 1. Kill line, re-derived and frozen before any return evidence was read

**Disclosure.** Before writing this section the agent had read only `hypothesis-backlog.md` lines 157 to 165 (the I1 entry, which includes its stated prior of 0.29 gross and Chen-Welch's 0.07), the cost report `2026-10-30-paper-fill-cost-arrival.md`, and `2026-10-30-strategy-first-ranking.md` (read for its cost convention; its AssetGrowth row repeats the same prior figures). No Chen-Zimmermann file, no frontier-report evidence section and no paper was opened. The line below uses no return figure.

**Inputs.**
- Cost per side (owner's fixed rung-1 rule: against arrival, equal-weighted): **+15.6 bp** for names over $200M median daily dollar volume, **+24.2 bp** for all names ([cost report](2026-10-30-paper-fill-cost-arrival.md), rung-1 table and liquidity table). Note: 15.6 is that report's buy figure in names over $200M; its per-side figure there is 10.6. The owner's rule fixes 15.6, so 15.6 is used (conservative).
- **Turnover assumption: 100% a year per leg** (each leg fully replaced once a year). Source: the signal re-sorts once a year (Cooper-Gulen-Schill form annually; Chen-Zimmermann `Portfolio Period` 12 for AssetGrowth, as used in the [ranking report](2026-10-30-strategy-first-ranking.md) row 7 "10% (annual)"), and the backlog I1 kill line's own 100% a year. With annual rebalancing, 100% is the ceiling (no name persisting in an extreme decile), so it is conservative; B3's measured 10.4% a month on gross profitability (about 125% a year) suggests a store implementation with rolling 10-K updates and delistings could run a little higher. UNVERIFIED for AssetGrowth itself.
- Each unit of turnover is two trades (a sale and a purchase), so a leg's yearly cost = 2 × 1.00 × cost per side.
- Borrow on the short leg: 30 bp a year = 0.025% a month.
- Margin: 2× the total cost, as in the backlog line.

**Arithmetic (% a month).**

| Item | at 15.6 bp/side (over $200M) | at 24.2 bp/side (all names) |
|---|---|---|
| One leg, trading: 2 × 100% × c / 12 | 2 × 15.6 / 12 = 2.60 bp = 0.026 | 2 × 24.2 / 12 = 4.03 bp = 0.040 |
| Two legs, trading | 0.052 | 0.081 |
| + borrow on short leg | +0.025 → 0.077 | +0.025 → 0.106 |
| **Long-short line (2× margin)** | **0.154 → 0.15** | **0.211 → 0.21** |
| Long leg cost; 2× | 0.026; 0.052 | 0.040; 0.081 |
| Short leg cost incl. borrow; 2× | 0.051; 0.102 | 0.065; 0.131 |

Check against the backlog: at 15 bp, two legs = 2 × 2 × 15 / 12 = 0.050, plus 0.025 = 0.075, × 2 = 0.15, matching the backlog's 0.15. At 24.2 bp the long-short line is 0.21, matching the direction draft's "about 0.21".

**Leg lines.** The backlog's 0.10 leg floor is kept where it is stricter than 2× cost. Long leg: max(0.10, 0.052 / 0.081) = **0.10** at both costs. Short leg (cost incl. borrow): max(0.10, 0.102 / 0.131) = **0.10 at 15.6 bp, 0.13 at 24.2 bp**.

**Which cost applies to which published series.** The book's own liquidity band is the names that pass its price and volume rules (about 1,900 to 2,100 a date, backlog I1 "Data"), most of them under $200M of median daily dollar volume, so the book's line is the **24.2 bp line**. The 15.6 bp line applies only to a value-weighted series (dominated by the largest, over-$200M names) and only if I1 would trade those names alone. The size-screened (ME above NYSE 20th percentile) series is judged at 24.2 bp.

**FROZEN KILL LINE (I1, rung 2):**
- **Book line (24.2 bp, all names):** FAIL if the long-short spread is under **0.21% a month**, or the long leg under **0.10**, or the short leg under **0.13**, or any of their t-statistics under **1.0**, post-publication (2009 onward) in liquid names.
- **Liquid-only line (15.6 bp, over-$200M names, value-weighted evidence only):** FAIL if long-short under **0.15**, or either leg under **0.10**, or t under 1.0.
- Legs are measured as excess over the market or the universe average where the source allows (the backlog convention); a source giving only long-short spreads cannot pass the leg tests (INSUFFICIENT on legs).
- Q1 (share issuance) is killed with I1 if both fail on the same series family (backlog I1 "Kill").

`FROZEN 2026-10-30 (session clock; wall time unavailable to this agent), before step 2. Not edited after this line was written.`

## Answer
**Verdict against the frozen line: FAIL overall.** The **liquid-only VW line FAILS**: in 2009-01 through 2025-12, the `AssetGrowth` VW deciles give L/S **−0.1721%/mo (t −0.6052)**, favorable long **−0.1158 (t −0.5679)**, and short **−0.0562 (t −0.3035)** versus the equal decile mean. All fall below the liquid-only thresholds (0.15 L/S, 0.10 each leg, t 1.0). The **size-screened book line PASSES** in that window: L/S **+0.4577 (t 2.2742)**, long **+0.1625 (t 1.1191)**, short **+0.2952 (t 1.8052)**; each clears its 0.21/0.10/0.13 and t 1.0 threshold. Under the report's own rule, a failure on either tested line fails the claim that the effect clears in liquid US names. The positive screened result is retained as counterevidence, not used to change the frozen rule. The stronger pre-declared 2010–2019 screen also fails its long-leg line (+0.0076, t 0.0603). Q1 independently fails its declared share-issuance read, so the coupled investment/issuance line retires at this desk read. Confidence is medium in CZ arithmetic, low in store transfer.

The local read-only [compute.py](/Users/josejuarez/tradepartner-probes/issuance/compute.py) and [out.txt](/Users/josejuarez/tradepartner-probes/issuance/out.txt) supply these means and ordinary t statistics (no HAC correction), using all ten portfolios in each file. `LS = 10 − 01` has zero monthly identity error. The 2009+ window has 204 complete months through 2025-12. The original research agent had no shell; this PR completes the declared computation without editing the frozen line. The 2016+ and 2020+ cuts in the output are exploratory. A PASS on either series alone would only advance to the limited-power store exam described in the backlog; it would not establish net alpha.

## Evidence
| Claim | Source | Tier | Key figure (quoted / as read) | OOS / post-pub? | Net of costs? |
|---|---|---|---|---|---|
| In-sample asset-growth spread is large equal-weighted and smaller value-weighted | Cooper, Gulen, Schill, JF 2008, via the repo's frontier report (FV-1) and search summaries | T1 via summary; UNVERIFIED in full text | 19.5% a year EW, 8.4% a year VW (1968-2003); CZ catalogue 1.73 a month, t 8.45 (EW deciles) | No (in-sample) | No |
| The VW spread after the general post-publication cut | Repo frontier report, "Economics" table; QI-2 (McLean-Pontiff 58%) | Own arithmetic on T1 | 0.70 × 0.42 = 0.29 a month gross | Haircut, not measured | No |
| Non-micro, post-2005: only profitability has a healthy median return; asset growth not among the named best | Chen, Welch, arXiv 2607.06502 (2026), §III (HTML; tables not rendered) | T1 (WP, unrefereed) | "Profitability is the only category with a healthy median return"; asset growth mentioned once, in §I, as an example anomaly | Yes (post-2005, partly in-sample for later papers; genuinely post-pub for CGS 2008 only from 2009) | No |
| Median non-micro post-2005 anomaly about 7 bp a month | Chen-Welch abstract (G2-S10) | T1 (WP) | "7 bp" | Yes | No |
| Average anomaly 4 bp a month net of costs and post-publication effects | Chen, Velikov, JFQA 2023, abstract (FV-2) | T1 | 4 bp a month; VW "11 bps per month, at best" (FEDS draft) | Yes | Yes |
| The VW investment factor (CMA, an asset-growth sort) was close to zero in 2010-2019 | Blitz, "Factor Performance 2010-2019: A Lost Decade?" (Robeco, 2020; Journal of ETFs, ETPs & Indexing per the Robeco copy), via secondary summaries (hedgefundalpha, advisorperspectives) | T2 via secondary; the Robeco PDF returned 404 and Advisor Perspectives 403. UNVERIFIED | Investment "0.22%" a year 2010-2019 (about 0.02 a month, my division); 6.76% a year 2000-2009 | Yes (2010-2019) | No |
| The investment factor's edge "dissipated" after 2004 | CFA Institute Enterprising Investor, "Fama and French: The Five-Factor Model Revisited" (2022), via search summary | T2, figure-free as read | "since 2004, the power of this factor has dissipated" | Yes | No |
| No asset-growth effect in low idiosyncratic-volatility firms | Lipson, Mortal, Schill, JFQA 46(6) 2011, abstract | T1 (abstract) | "there appears to be no asset growth effect in firms with low IVOL" | In-sample | No |
| Published factors mostly replicate (counter-evidence) | Jensen, Kelly, Pedersen, JF 2023, via search summaries (PDF unreadable here) | T1 via summary | US baseline replication about 55.6%; more than 75% in 10 of 13 themes, the exceptions being seasonality, leverage and size (so investment by implication replicates, UNVERIFIED) | Mostly in-sample and international, not post-2008 US large-cap | No |
| The CZ files that decide the question exist | Hugging Face API tree, OpenSourceAP/CrossSection, read 2026-10-30 | T1 (dataset) | `PredictorAltPorts_DecilesVW.parquet` (25.7 MB), `PredictorAltPorts_LiqScreen_ME_gt_NYSE20pct.parquet` (18.8 MB); only CSV is `SignalDoc.csv` | n/a | n/a |
| Our cost | [Cost report](2026-10-30-paper-fill-cost-arrival.md), rung-1 table | Own result (paper fills, 3 sessions) | +24.2 bp a side all names; buys over $200M +15.6 | Paper only | n/a |

**Against the frozen line (% a month, 2009-01 onward, ten deciles):**

| Series | L/S mean (t) | Long vs decile mean (t) | Short vs decile mean (t) | Applicable line | Verdict |
|---|---:|---:|---:|---|---|
| CZ AssetGrowth VW | −0.1721 (−0.6052) | −0.1158 (−0.5679) | −0.0562 (−0.3035) | Liquid-only 0.15 / 0.10 / 0.10, all t ≥ 1 | **FAIL** |
| CZ AssetGrowth size-screened | +0.4577 (2.2742) | +0.1625 (1.1191) | +0.2952 (1.8052) | Book 0.21 / 0.10 / 0.13, all t ≥ 1 | **PASS** |

The 2010–2019 declared window also shows VW L/S −0.1796 (t −0.6514), long −0.2596 (t −1.4384), short +0.0800 (t 0.4330); screened L/S +0.3924 (t 2.0559), long +0.0076 (t 0.0603), short +0.3848 (t 2.5053). Both fail their applicable line in that window.

Note on the CMA proxy: CMA uses 30th and 70th percentile breakpoints, not deciles, so it understates a decile spread. Even at three times 0.02 it is below 0.15. It also omits 2020 onward (2022 was a strong year for value and investment, UNVERIFIED).

## Disconfirmation
- **Searches run (2026-10-30), aimed at both sides:**
  1. Asset growth after publication in large or value-weighted stocks after 2008.
  2. The delisting-return and external-financing critique.
  3. Fama-French CMA in 2010-2019 (two searches, plus the Blitz PDF and Advisor Perspectives, both blocked).
  4. Chen-Zimmermann AssetGrowth post-publication, and McLean-Pontiff decay by signal.
  5. Chen-Welch HTML for asset growth and investment.
  6. JKP replication by theme (one search; the CBS publisher PDF and two summaries could not be read).
  7. Lipson-Mortal-Schill on where the effect sits.
  8. Hou-Xue-Zhang I/A with NYSE breakpoints.
  9. CMA since 2008.
- **Against the claim (that I1 clears the line):**
  - Chen-Welch's category statement.
  - CMA close to zero in 2010-2019.
  - CFA Institute: the factor "dissipated" after 2004.
  - Lipson-Mortal-Schill: no effect in low-IVOL firms, which are the liquid names our band holds.
  - A working paper (core.ac.uk 35456666, author and year not read: the PDF returned 403) argues the negative-growth leg's return comes from omitted delisting returns, with "no independent effect" once delisting and external financing are controlled. UNVERIFIED, unpublished as far as found. If it holds, it also cuts against a long leg read on survivors.
  - A HKUST thesis says the effect vanishes where arbitrage is easy (search summary only).
- **For the claim:**
  - The VW in-sample spread is positive and significant (8.4% a year, t about 5 per the frontier report), so the effect is not micro-cap only.
  - JKP find high replication rates, with investment not among the failing themes.
  - Chen-Zimmermann find publication bias small (about 12 to 13% of in-sample return).
  - Lipson-Mortal-Schill call the effect "pervasive" (secondary excerpt).
  - None of these is a post-2008 US liquid-name figure.
- **No post-2008 asset-growth-specific figure was found on either side.** The frontier report's search (2026-10-28) found none either.

## Caveats & gaps
- The verdict now rests on the declared CZ series. The CMA and category figures remain contextual proxies, not decision evidence. The screened VW conflict is material and retained.
- The repo's working tree was read without `git fetch` (no shell). Files may differ from origin/main.
- Before freezing the kill line, the agent had seen the backlog's stated prior (0.29 and 0.07) and the ranking report's AssetGrowth row. Neither entered the line's arithmetic, which uses only cost, turnover and borrow.
- The 15.6 bp figure is a buy-side cost used as a per-side cost, as the owner's rule fixes. The liquid per-side figure is 10.6.
- Turnover of 100% a year per leg is an assumption (UNVERIFIED for AssetGrowth). A store version updated on each 10-K could run higher, and turnover of 125% a year would raise the book line to about 0.25.
- Borrow of 30 bp a year assumes general-collateral names. High-asset-growth names may include hard-to-borrow issuers (LS-6: the short legs carry most of the profit and the borrow).
- The "post-publication" window for CGS (2008) is 2009 onward. Chen-Welch's "post-2005" includes 2005-2008, which is in-sample for CGS's sample end (2003) only partially.

## UNVERIFIED items
- CMA 2010-2019 = 0.22% a year (secondary summaries of Blitz 2020; Ken French file not read).
- The CFA Institute "dissipated since 2004" wording (search summary).
- The delisting-bias working paper's author, year and figures.
- JKP's investment-theme replication rate (implied from "10 of 13 themes", not read).
- Lipson-Mortal-Schill's large-firm results beyond the abstract.
- Turnover (100% a year per leg) and borrow (30 bp a year) inputs.

## Follow-up questions (not answered here)
1. Completed here: CZ AssetGrowth and Q1 ShareIss1Y were computed from the local parquets in the declared windows; both ideas fail their governing lines. No further historical exam is proposed.
2. Is the delisting-bias critique a citable paper, and does CZ's `AssetGrowth` include delisting returns? (CZ uses CRSP with delisting adjustments, UNVERIFIED.)
3. What is the measured turnover of a 10-K-updated asset-growth decile in our universe? It sets the line between 0.21 and about 0.25.

## Sources
- Repo: [hypothesis backlog](hypothesis-backlog.md) I1 (lines 157-165); [paper fill cost vs arrival](2026-10-30-paper-fill-cost-arrival.md); [strategy-first ranking](2026-10-30-strategy-first-ranking.md) row 7 and §3; [strategy frontier verified](2026-10-28-strategy-frontier-verified.md) "Economics", "Evidence", "Disconfirmation"; [claims.toml](claims.toml) QI-2, QI-15, G2-S10, FV-1, FV-2.
- Chen and Zimmermann, Open Source Asset Pricing, [Hugging Face file tree](https://huggingface.co/api/datasets/OpenSourceAP/CrossSection/tree/main?recursive=true) (release 2026.10). T1.
- Chen and Welch, "What Useful Alphas?" (2026), [arXiv HTML](https://arxiv.org/html/2607.06502), §I, §III, §VI. T1 (WP).
- Chen and Velikov, JFQA 2023, [Cambridge](https://www.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/zeroing-in-on-the-expected-returns-of-anomalies/945133D5A3ECEEAF466AEE91551FD225) (abstract, via the frontier report). T1.
- Blitz (2020), [Robeco PDF](https://robeco.com/files/docm/docu-robeco-factor-performance-2010-2019-a-lost-decade.pdf) (404 on fetch); summaries: [hedgefundalpha](https://hedgefundalpha.com/strategies/a-lost-decade-for-the-fama-french-factors/), [Advisor Perspectives](https://www.advisorperspectives.com/articles/2020/05/13/a-lost-decade-for-the-fama-french-factors) (403), [CXO](https://www.cxoadvisory.com/strategic-allocation/equity-factor-performance-during-the-2010s/). T2 via secondary.
- CFA Institute, [Fama and French: the five-factor model revisited](https://rpc.cfainstitute.org/blogs/enterprising-investor/2022/fama-and-french-the-five-factor-model-revisited/) (search summary). T2.
- Lipson, Mortal, Schill, JFQA 2011, [RePEc](https://econpapers.repec.org/RePEc:cup:jfinqa:v:46:y:2012:i:06:p:1651-1682_00), [Cambridge](https://resolve.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/on-the-scope-and-drivers-of-the-asset-growth-effect/B8CFCF68A82E273E979221EF26A77096) (abstract). T1.
- Delisting-bias working paper, [core.ac.uk 35456666](https://core.ac.uk/download/35456666.pdf) (403; search summary only); HKUST thesis, [two essays on stock market anomalies](https://researchportal.hkust.edu.hk/en/studentTheses/two-essays-on-stock-market-anomalies/). Unrefereed, UNVERIFIED.
- Jensen, Kelly, Pedersen, JF 2023, [NBER w28432](https://nber.org/system/files/working_papers/w28432/w28432.pdf), [CBS publisher version](https://research-api.cbs.dk/ws/portalfiles/portal/95651880/theis_ingerslev_jensen_et_al_is_there_a_replication_crisis_in_finance_publishersversion.pdf) (unreadable here); critique [Systematically Biased](https://systematicallybiased.substack.com/p/pockets-of-replicability-post5) (T3). T1 via summary.
- Hou, Xue, Zhang, "Replicating Anomalies", [NBER w23394](https://www.nber.org/papers/w23394.pdf) (search summary: 64% insignificant with NYSE breakpoints and VW; I/A row not read). T1.
- Web calls: 18 (11 searches, 7 fetches; 6 fetches blocked or unreadable).
