# Research Report: Does Google search volume predict returns or revenue surprises out of sample, net of costs, outside microcaps? (G5)

**Brief:** #229  ·  **Date:** 2026-09-26  ·  **Status:** INCOMPLETE. Every done-when item is present: a §6.1 grade, the replications table, the stability and terms paragraph with primary-source quotes, the disconfirmation log, and the recommendation line. The budget is spent: 12 of 12 sources, 19 of 20 searches.
- **Pass 2:** the coordinator supplied full text for S2 and S8. No new sources, no new searches. Their figures below now come from the text.
- **Still abstract- or summary-level:** S1, S3 and S9. Their key figures remain UNVERIFIED (see "Open items").

**Agent/model:** research agent, team blanc, claude-opus-5-5

This replaces handoff H6 (INSUFFICIENT: one source, no disconfirmation search). All sources were retrieved on **2026-09-26**.

## Answer
The brief's question has two variants, and they get different grades.

**Claim A. Search volume for a stock (ticker or company-name "attention") predicts returns out of sample, post-publication, net of costs, in non-microcap US stocks.**
**Verdict: NOT SUPPORTED**  ·  **Confidence:** low–medium
- **The original result is in-sample and gross of costs.**
  - On Russell 3000 stocks from 2004 to 2008, a rise in search volume predicts higher prices over the next 2 weeks and a reversal within the year [S1, abstract].
  - The same authors later describe that result as predictability for short-term returns "especially among small stocks" [S2 §1, full text].
- **The only US large-cap replication found reverses the sign.** On S&P 500 stocks from 2008 to 2013, "high Google search volumes lead to negative returns". A long-low/short-high search strategy "is profitable when the transaction cost is not taken into account but is not profitable if we take into account transaction costs" [S3, abstract].
- **S8 (full text) is a sliding-window backtest on S&P 100 components.** It uses ticker and company-name keywords, weekly data, and 2 bps per transaction.
  - The Google Trends-only predictors earn 18.3 bps/week (t = 2.93). Past-returns-only predictors earn 16.9 bps/week (t = 2.72). Both together earn 17.1 bps/week (t = 2.73) [S8 Table 2].
  - The Google-only and price-only results are not different (Wilcoxon p = 0.72). The authors suspect "the backtest system just learns to recognize trends unconditionally, in other words, that the predictors are simply equally useless" (§2.8).
  - With binary inputs, the Google-only predictors do worse than price-based ones (Fig. 4), "however, other machine learning methods yield the opposite result" (§2.8).
  - Conclusion: "We have not been able to show that Google Trends data contain more exploitable information than price returns themselves" (§3).
- **Why NOT SUPPORTED rather than MIXED.**
  - The one credible post-publication replication in large caps does not find the original effect: its sign flips, and the flipped strategy does not survive costs [S3].
  - The one large-cap backtest net of costs finds no predictability beyond what past prices already give [S8].
  - Neither confirms S1's 2-week price-pressure effect outside small stocks.
  - This is still near the MIXED boundary: S3 is read only at abstract level, and S8's Google-only backtest is profitable in its own right, just not incrementally.

**Claim B. Product-level search volume nowcasts revenue surprises and yields a tradable return (the H6 variant).**
**Verdict: INSUFFICIENT** as a tradable, net-of-cost signal  ·  **Confidence:** medium
- **Revenue nowcasting is consistent across two Tier 1 sources.**
  - **S2 (full text, April 2010 draft, 2004–2008).** The sample is 865 publicly traded firms that advertised on national TV per Nielsen. It skews large: mean log market cap is 8.277 against 5.307 for the CRSP/COMPUSTAT universe (Table 2).
    - The change in product search volume predicts the non-seasonally-adjusted revenue surprise: coefficient 0.825 (s.e. 0.084), rising to 0.919 (0.082) with controls (Table 3, top panel).
    - A one-standard-deviation rise in search volume "corresponds to an increase in standardized unexpected revenues by .20 (t-stat = 9.86)" (§3).
    - Seasonally adjusted, the coefficient is 0.487 (0.094). It falls to 0.116 (0.049) once the lagged revenue surprise is controlled for (Table 3, bottom panel).
  - **S9 (CAR 2025; ~200 US retailers, 2004–2019).** Search intensity "is predictive of analyst nowcast and forecast errors" after controls (abstract snippet).
- **The return part is not established.**
  - **S2 is in-sample and gross, with no trading strategy or cost analysis.** It gives:
    - a 3-day announcement abnormal return coefficient of 95.1 bps (s.e. 36.0), t = 2.64;
    - a one-standard-deviation effect of "about 20 basis points over the three-day period (about 17% annualized)" (§6, Table 6);
    - post-announcement drift of 130.3 bps (s.e. 76.8), significant only at 10%, and not significant (122.9, s.e. 75.6) once the announcement return is controlled for (Table 7).
  - **S9's only return figure is Tier 3 and UNVERIFIED.** It is "2–3% higher" returns, concentrated at announcements, from a university summary that says nothing on costs.
  - **Nothing found either way on the tradable version.** No net-of-cost, out-of-sample return test of the product variant was found, and no failed replication either.
- **Size and coverage.**
  - S2's sample is mostly large firms, so the revenue link is not a microcap artefact.
  - But analyst-surprise predictability is confined to high-dispersion firms: 0.134 (s.e. 0.081) for above-median forecast dispersion against −0.017 (s.e. 0.015) below it (Table 5). Across all firms it is 0.098 (s.e. 0.052), t = 1.89.
  - S9's summary says predictability is "more than 80% stronger" with limited analyst coverage (Tier 3).
  - So the part analysts miss sits in harder-to-forecast firms.

**Data verdict, independent of the grades.**
- **The data are sampled and unstable.** Google Trends output is a sample, and the same query returns different series on different days (S4, S10, S11, S12). The noise is worst for low-volume terms, which describes most single-firm product names.
- **Series are rescaled to their own peak and have been revised.**
  - S2's data were "weekly search volume scaled by a constant: the maximum search volume over the search period" (§2.1).
  - S8 documents that the data "are constantly being revised", were reformatted in 2012 to integers 0–100, have precision "about 5%", and that "any new maximum increases the granularity of the data" (§2.5).
- **History is limited and access is restricted.** The official API offers only "a rolling window of the last 5 years" and is an application-gated alpha [S7]. Google's terms bar automated access that violates robots.txt [S5], and trends.google.com's robots.txt disallows the `/explore?` pages [S6].
- **Keyword choice is itself a look-ahead risk.** S2's product queries were chosen by research assistants, with ties broken by Google Insights' related-searches feature as of about 2009 (§2.1, fn. 2). S8 warns that keywords chosen with later knowledge put "information from the future into the past" (§2.4).
- **Consequence:** an honest point-in-time backtest of either variant is not possible with data available today. Only a forward, timestamped collector could build history.

**One-line recommendation (per the brief):** Keep only the revenue-nowcast variant, as a deferred candidate that cannot be backtested until point-in-time product-search history exists. Retire the ticker-search return variant.

(Pass 2 did not change either grade or the recommendation. See "Pass 2 note".)

## Replications and extensions

| # | Study | Tier | Sample period | Universe | Variant and timing | Net result |
|---|---|---|---|---|---|---|
| S1 | Da, Engelberg & Gao, *JF* 66(5) 2011 (original) | 1 | 2004–2008 | Russell 3000 | Ticker SVI; weekly abnormal SVI (ASVI); returns over the next 2 weeks, reversal within a year | **Gross, in-sample.** Higher prices over 2 weeks, "eventual price reversal within the year" (abstract, per search results). Per S2 §1 (same authors): "predictability for short-term returns, especially among small stocks". Magnitude **UNVERIFIED** (summaries give both "+18.7 basis points" and "more than 30 basis points" per one-s.d. ASVI) |
| S3 | Bijl, Kringhaug, Molnár & Sandvik, *IRFA* 45 (2016): 150–156 | 1 | 2008–2013 (abstract); a secondary summary says data run Jan 2007–Dec 2013, 431 firms, 313 weeks (**UNVERIFIED**) | S&P 500 | Company-name search; weekly; long low-search / short high-search | **Sign reversed:** "high Google search volumes lead to negative returns". The strategy is "profitable when the transaction cost is not taken into account but is not profitable if we take into account transaction costs" (abstract). Post-publication window (S1's working paper circulated in 2009). Cost level **UNVERIFIED** |
| S8 | Challet & Bel Hadj Ayed, arXiv 1403.1715 (2014); full text | 1 (WP; one author at Encelade Capital) | Backtest ~2004/05–2013. Weekly Google Trends data downloaded 2013-04-21. Calibration window "about 6 months" with sliding in/out-of-sample windows (§2.2, §2.8) | Components of the S&P 100 (§2.3); SPY for the Preis et al. re-test | Ticker and company-name keywords (following DEG) plus the authors' own keywords; non-linear ensemble learning; weekly | **Net of 2 bps per transaction:** Google-only 18.3 bps/week, IR 0.99, t 2.93; returns-only 16.9, IR 0.92, t 2.72; both 17.1, IR 0.92, t 2.73 (Table 2). Not different (p = 0.72). Preis-style fixed rule on SPY, averaged over keywords and k = 1…100: Sharpe "about 0.12 and a t-stat of 0.37", "flat from 2011 onwards, i.e. out of sample" (§2.7). Random pre-2004 keywords (illnesses, classic cars, arcade games) give t-stats as large as Preis's keywords, e.g. "Moon Patrol" 2.7 (Table 1) |
| S2 | Da, Engelberg & Gao, "In Search of Earnings Predictability", draft of April 9, 2010 ("Preliminary and Incomplete"); full text | 1 (WP) | 2004–2008 (quarterly) | 865 public firms that advertised on national TV (Nielsen, matched to COMPUSTAT from 9,764 advertisers); 12,259 products; 337 single-product firms. The most popular product is chosen by ad count. Larger than the universe (mean log size 8.277 vs 5.307) | Product SVI change (log, quarter on quarter or year on year) before the earnings announcement | **Gross, in-sample, regression only; no strategy, no costs.** Revenue surprise 0.825***, rising to 0.919*** with controls (Table 3). SUE 1.221** (Table 4). Analyst surprise 0.098* overall, significant only in high-dispersion firms (Table 5). 3-day announcement CAR 95.1*** bps per unit of SVI change, ≈ 20 bps per s.d. (Table 6). Post-announcement CAR 130.3* bps, not significant after controlling for the announcement return (Table 7) |
| S9 | Lind & Ramesh, *CAR* 42(3) 2025: 1557–1588 | 1 (abstract snippet) + Tier 3 summary for return figures | 2004–2019 | "Nearly 200" US listed retailers | Firm-level search intensity; quarterly nowcast of revenue against analyst expectations | Predicts "analyst nowcast and forecast errors" after controlling for past sales, deferred revenue, firm characteristics and fixed effects (abstract snippet). The aggregate index nowcasts retail sales "both within and out-of-sample". Returns "2–3% higher", concentrated at announcements (Tier 3; **UNVERIFIED**; costs not stated) |

Not found (see log):
- any US post-2011 re-test of S1's positive 2-week price pressure that confirms it in non-microcaps;
- any net-of-cost test of the product variant;
- any failed replication of S2.

## Evidence

| Claim | Source | Tier | Key figure (quoted) | OOS / post-pub? | Net of costs? |
|---|---|---|---|---|---|
| Ticker SVI predicts 2-week price pressure then reversal | S1 DEG 2011 | 1 | "predicts higher stock prices in the next 2 weeks and an eventual price reversal within the year"; "Russell 3000 stocks from 2004 to 2008"; "likely measures the attention of retail investors" (abstract text via search results) | No | No |
| S1's effect is concentrated in small stocks | S2 §1 (full text), describing DEG (2009), the WP of S1 | 1 (WP) | "stock-ticker search volume reflects retail demand for shares and has predictability for short-term returns, especially among small stocks" | n/a | n/a |
| In S&P 500 stocks after 2008, the sign flips and the trade fails net of costs | S3 Bijl et al. 2016 | 1 | See table (EconPapers abstract, verbatim) | Yes (later sample) | **Yes: not profitable** |
| Google Trends predictors add nothing over past returns in S&P 100 stocks | S8 Table 2, §2.8, §3 (full text) | 1 (WP) | 18.3 vs 16.9 vs 17.1 bps/week; t 2.93 / 2.72 / 2.73; p = 0.72; "not been able to show that Google Trends data contain more exploitable information than price returns" | Sliding in/out-of-sample windows; the authors flag "tool bias" (§2.8) | Yes, 2 bps per transaction (low for retail) |
| Index-level Google Trends rule fails out of sample; keyword-selection bias | S8 §2.4, §2.7, Table 1 | 1 (WP) | Preis-style rule averaged over keywords: Sharpe ≈ 0.12, t 0.37, flat from 2011. Random keywords match Preis's t-stats. Kristoufek (2013) "clearly shows that the relationship between SVI and future returns has dramatically changed in 2008" (§2.2, second-hand) | Yes (post-2011 flat) | Yes (2 bps; costs "subtract about 15% to the performance") |
| Product SVI nowcasts revenue surprises | S2 §3, Table 3 (full text) | 1 (WP) | 1 s.d. → SUS +0.20, t 9.86. Coefficients: 0.825 (0.084) unadjusted; 0.487 (0.094) seasonally adjusted, 0.116 (0.049) with lagged surprise. N = 11,727 / 9,516 | No (in-sample, sector and year fixed effects) | n/a |
| Product SVI → EPS surprise, analyst surprise | S2 Tables 4–5 | 1 (WP) | SUE 1.221 (0.603); without special items 0.383 (0.238). Analyst surprise 0.098 (0.052) overall; high-dispersion 0.134 (0.081); low-dispersion −0.017 (0.015) | No | n/a |
| Product SVI → announcement return | S2 §6, Tables 6–7 | 1 (WP) | 3-day CAR 95.086 (36.017), t 2.64; ≈ 20 bps per s.d. over 3 days; 78.4 (34.9) controlling for the current revenue surprise. Post-announcement 130.3 (76.8), 122.9 (75.6) with the announcement return | No | **No** (no strategy, no costs) |
| Search intensity predicts analyst revenue errors to 2019 | S9 Lind & Ramesh 2025 | 1 | Abstract snippet; Wiley 403 | Aggregate OOS: yes. Firm level: later sample | Unknown |
| Trends data are a sample, with noise | S4 Google Trends Help FAQ | 1 (vendor doc) | "While only a sample of Google searches are used in Google Trends…"; "statistical noise that includes small and random fluctuations that don't represent actual search behavior"; noise "most noticeable on queries with low or no search interest" | n/a | n/a |
| Trends data are revised, rounded and rescaled to each series' maximum | S8 §2.5 (full text) | 1 (WP) | "these data are constantly being revised"; values "would change within the given error bars every time one would download data for the same keyword"; since 2012 "integer numbers between 0 and 100, 100 being the maximum"; "precision is about 5%"; "any new maximum increases the granularity of the data". Also "not reliably available before 6 August 2008" (citing Wikipedia, second-hand) | n/a | n/a |
| SVI normalised to the window maximum | S2 §2.1 (full text) | 1 (WP) | "SVI is calculated as weekly search volume scaled by a constant: the maximum search volume over the search period". Low-volume terms return "an error message" | n/a | n/a |
| The same query varies day to day | S10 Cebrián & Domenech, *TFSC* 202 (2024) | 1 | "the same query produces different results that can widely change from day to day" (RePEc abstract, verbatim) | n/a | n/a |
| Sampling noise is substantial; frequencies are inconsistent | S11 Eichenauer et al., *Economic Inquiry* 60(2) 2022: 694–705 | 1 | "raw data are frequency‐inconsistent: daily data fail to capture long‐run trends"; "sampling noise can be substantial" (search snippet of the abstract) | n/a | n/a |
| Reliability falls with term frequency | S12 Gummer & Oehrlein, *SSCR* 2025 | 1 | 62 non-real-time samples per high-frequency term (30 for low-frequency), Germany. High-frequency terms carry "comparatively smaller risk of reliability issues" (search snippets; Sage 403) | n/a | n/a |
| Official API: 5-year window, alpha | S7 Google Trends API page | 1 (vendor doc) | "Get early access to the Google Trends API alpha"; "a rolling window of the last 5 years of data"; "Consistently scaled data" | n/a | n/a |
| Automated access terms | S5 Google ToS; S6 trends.google.com/robots.txt | 1 (primary) | See next section | n/a | n/a |

### §6.1 check
- **Claim A (ticker search → returns).**
  - For: S1 only. It is in-sample and gross, and by its authors' own description concentrated in small stocks (S2 §1).
  - Against:
    - S3: post-publication, S&P 500, fails net of costs.
    - S8: S&P 100, net of 2 bps; no incremental predictability over price data, p = 0.72. The index-level rule is flat after 2011.
  - No out-of-sample or post-publication test confirms S1 in non-microcaps.
  - **NOT SUPPORTED**, at the MIXED boundary. Only one replication (S3) tests S1's design directly, and it was read at abstract level.
- **Claim B (product search → revenue → returns).**
  - Two Tier 1 sources (S2 full text, S9 abstract) agree on revenue and analyst-error nowcasting.
  - Only one qualifying return result exists: S2 Table 6, in-sample, gross, about 20 bps per s.d. over 3 days. S9's return figure is Tier 3.
  - There is no cost test and no disconfirming replication.
  - For the brief's return question: **INSUFFICIENT** (fewer than 2 qualifying return sources).
- **Source mix.** 6 of 12 sources are disconfirming, which meets the brief's half requirement:
  - Returns: S3, S8.
  - Data instability: S4, S10, S11, S12. S8 §2.5 also documents instability.
  - Neutral primary documents: S5, S6, S7. Supportive: S1, S2, S9.

## Google Trends data stability and terms

**Stability.**
- **Google's own FAQ.** Trends uses "only a sample of Google searches" and incorporates "statistical noise that includes small and random fluctuations that don't represent actual search behavior", "most noticeable on queries with low or no search interest" [S4].
- **Independent studies confirm the instability:**
  - The SVI "is computed using a sample of the searches", and "the same query produces different results that can widely change from day to day" [S10].
  - Raw data are "frequency‐inconsistent", with substantial sampling noise [S11].
  - Reliability is worst for low-frequency terms [S12].
- **Revisions and rescaling**, as distinct from sampling:
  - S8 reports that the data "are constantly being revised". Values "would change within the given error bars every time one would download data for the same keyword".
  - The 2012 switch to integers 0–100 ("100 being the maximum of the time-series") hides small changes behind rounding, with precision "about 5%". "Any new maximum increases the granularity of the data, thereby making it even less reliable" (§2.5).
  - S2 confirms that SVI is "scaled by … the maximum search volume over the search period" (§2.1).
  - So a series downloaded today is normalised using the future maximum of the requested window. That is a look-ahead channel, now quoted rather than inferred.
- **Why this matters for H6.**
  - A single company's product name is usually a low-frequency term. In S2, terms without enough volume returned "an error message" (§2.1).
  - The series observable on a given past date cannot be recovered.
- **Coverage limits.** The official API offers "consistently scaled data", but only "a rolling window of the last 5 years of data". It is an application-gated alpha ("We're now accepting applications for alpha testers") [S7].

**Terms.**
- **Google's Terms of Service** (page retrieved 2026-09-26; it shows "Effective July 30, 2026") prohibit "using automated means to access content from any of our services in violation of the machine-readable instructions on our web pages (for example, robots.txt files that disallow crawling, training, or other activities)" [S5].
  - The ToS also point to "service-specific additional terms". None were found for Trends within budget.
- **robots.txt.** `https://trends.google.com/robots.txt` (retrieved 2026-09-26) reads `User-agent: *`, `Disallow: /explore?`, `Disallow: /trends/explore?` [S6].
- **UNVERIFIED:**
  - whether that is the complete file (it came through a summarising fetcher);
  - whether it covers the internal endpoints that unofficial libraries call (e.g. `/trends/api/…`).

  Any automated collector should be checked against both before it is built, or should use the official API if access is granted.
- **Not restricted by the quoted text:** manual CSV export and the API route.

## Disconfirmation log
Budget: 20 searches, 12 sources. **Used: 19 searches, 12 sources.** Pass 2 added no searches and no sources; it read the full text of two counted sources (S2, S8). Web-fetch calls on already-counted sources, failed fetches and out-of-scope reads are listed but not counted as sources.

| # | Search (verbatim intent) | Aim | Found / not found |
|---|---|---|---|
| 1 | DEG "In Search of Attention" replication, post-publication | Failed replications of S1 | Only the original; **no replication paper found** |
| 2 | Google search volume stock returns out-of-sample transaction costs S&P 500 (Bijl et al.) | Net-of-cost US large-cap test | **S3** (sign reversed; unprofitable net) |
| 3 | DEG "In Search of Earnings Predictability" abstract | Baseline for product variant | S2 (summaries in pass 1; full text in pass 2) |
| 4 | Google Trends sampling inconsistency, same query different days | Data instability | Led to S10, S11, S12; S4 fetched directly |
| 5 | Google Trends ToS, automated queries, API alpha | Terms | Only Tier 3 scraper-vendor pages. Went to primary S5, S6, S7 directly |
| 6 | ASVI returns, later sample, effect disappears, small stocks only | Microcap confinement / post-publication decay | Summaries of S1 saying the effect is stronger in small stocks (now confirmed by S2 §1). **No US post-publication re-test found**. Financial Innovation systematic review behind a cookie wall, not read |
| 7 | Ben-Rephael, Da & Israelsen, "It Depends on Where You Search" | Whether retail search attention is only temporary pressure | RFS 2017 abstract: retail attention "results in positive and temporary price pressure". **Not counted** (consistent with S1; no net-of-cost return test) |
| 8 | Challet & Bel Hadj Ayed | Look-ahead / keyword-bias critique | **S8** |
| 9 | Product search revenue surprise out-of-sample, analysts already incorporate, later sample | Post-publication test of product variant | S9 (via Rice summary). Teoh et al. (AOS 2023) on revenue management found, not read |
| 10 | Google search volume stock returns US firms post-2011, not significant, large firms | Post-publication failures | Only S3 again; other hits non-US (out of scope) |
| 11 | CAR 2025 Google search retailers 2004–2019 | Identify S9 | Title found |
| 12 | Lind & Ramesh exact title, abstract | Abstract of S9 | Abstract snippet (Wiley 403) |
| 13 | Cebrián & Domenech TFSC 2024 abstract | Instability | **S10** |
| 14 | Eichenauer et al. abstract, sampling error | Instability | **S11** |
| 15 | DEG extended sample 2009–2018, weaker/insignificant | Post-publication decay in the US | **Not found.** Min (arXiv 2101.03239): Russell 3000 before vs after 2009. Abstract gives no result and the PDF was binary, so **not counted; findings UNVERIFIED** |
| 16 | Gummer & Oehrlein 2025 | Instability | **S12** |
| 17 | Gummer & Oehrlein results sentences | Results for S12 | Snippet-level results |
| 18 | DEG "basis points", smaller stocks, Russell 3000 | Magnitude and size concentration in S1 | Conflicting summaries (18.7 bp vs >30 bp). **UNVERIFIED**. The small-stock concentration is now confirmed via S2 §1 |
| 19 | DEG Earnings Predictability sample and figures | S2 figures | Pass-1 summaries were **wrong**: they gave a Russell 3000 sample and a "few products / growth / earnings-management" split. Neither is in the April 2010 full text. Corrected in pass 2 |

**Found against:**
- S3: the sign reverses post-publication in the S&P 500, and the trade is unprofitable net of costs.
- S8:
  - no incremental predictability over price data in S&P 100 stocks net of 2 bps;
  - the index-level rule is flat after 2011;
  - random keywords match "finance" keywords;
  - the data are revised and rounded.
- S4, S10, S11, S12: the data are sampled, noisy and irreproducible, worst for low-volume terms.
- S7: only 5 years of history via the API.
- S2 against itself:
  - no out-of-sample test, no strategy, no costs;
  - analyst-surprise predictability is only in high-dispersion firms;
  - post-announcement drift is not significant once the announcement return is controlled for;
  - keyword choice used a ~2009 Google Insights related-search tool (a hindsight risk that S8 §2.4 names).

**Searched and not found:**
- a US replication that confirms S1's positive price pressure after 2008;
- any net-of-cost return test of the product variant;
- any failed replication of the revenue-nowcast result;
- Google Trends service-specific terms.

(Revisions are now documented, by S8 §2.5.)

**Read and excluded (not counted):**
- Yoshinaga & Rocco (BBR 2020): Brazil, non-US, out of scope. Its sign also matches S3.
- Min (2021): results unreadable.

**Failed fetches:**
- DEG PDFs at NBER (two URLs), nd.edu and ou.edu; the S2 and Min PDFs (binary). S2 was later supplied as full text.
- Semantic Scholar API (429).
- HKUST portal (404).
- ScienceDirect, Wiley and Sage (403).
- Springer (cookie wall).

## Caveats & gaps
- **S1, S3 and S9 are still abstract- or summary-level.** Table-level figures for S1 (magnitude) and S3 (coefficients, cost assumption) are missing.
- **The Claim A grade rests on one direct replication (S3) plus S8's no-incremental-value result.** A second post-2011 US non-microcap test could move Claim A to MIXED if positive; Min (2021) is the obvious candidate.
- **S8's Google-only backtest is itself profitable net of 2 bps** (18.3 bps/week, t 2.93). The authors read this as the machine learning system learning trends unconditionally, not as search-volume information: it matches price-only, and binary-input results depend on the method.
  - 2 bps per transaction is low for a retail account.
  - The authors flag "tool bias": the heavy machine learning methods were not available for most of the backtest period.
  - Its gross/net exposure reached 4.5 (Fig. 5), so it is not a long-only design.
- **S2 is a preliminary 2010 draft.** Its figures may differ from any published version, and whether it was published, and where, is **UNVERIFIED**.
  - The "few products, growth firms, earnings management" split that search summaries attributed to S2 is **not in this draft**. It may come from a later version (**UNVERIFIED**).
  - S2 depends on Nielsen TV-advertising data to map firms to products. Whether an equivalent point-in-time mapping is available to this project is not researched.
- **S2's announcement-return effect is small against costs.** About 20 bps per s.d. over 3 days, once a quarter, in-sample and gross. That it would be small relative to a retail round trip is my inference, not a tested result.
- **S3's sample overlaps S1's publication date,** not just its working-paper date. That is post-circulation, not strictly post-journal-publication.
- **S9's firm-level result is analyst-error prediction,** not a traded return net of costs, and the sample is retail firms only. Its "2–3%" and "80% stronger" figures come from a Tier 3 university summary.
- **The robots.txt interpretation is not legal advice.** The file came through a summarising tool (see Terms).

## UNVERIFIED items
- S1 magnitude: "18.7 bp" vs "more than 30 bp" per one-s.d. ASVI. (Small-stock concentration is now confirmed qualitatively via S2 §1; no figures.)
- S2 publication status. The "few products / growth / earnings management" split (not in the April 2010 draft).
- S3 exact sample (2007–2013, 431 firms, 313 weeks, per a secondary summary), coefficients, and the transaction-cost level used.
- S9 return figures ("2–3%"), whether they are net of costs, and the query construction (product, brand or company name).
- S8's second-hand claims: Kristoufek (2013) on the 2008 regime change; Google Trends availability before 6 August 2008 (it cites Wikipedia).
- Completeness of the robots.txt quote. Coverage of `/trends/api/` endpoints.
- Min (2021): pre- vs post-2009 results.

## Open items (why INCOMPLETE)
1. Full-text read of S1: the table of ASVI against future returns, split by size.
2. Full-text read of S3: the cost assumption and coefficients.
3. Primary text of S9 (Lind & Ramesh, CAR 2025): the return test and costs.
4. Read Min (arXiv 2101.03239), the only US post-2009 re-test located.
5. Google Trends service-specific terms, if any exist.

Closed in pass 2: S2 sample, coefficients, announcement returns and cost treatment; S8 sample, universe, costs and results.

## Follow-up questions (not answered here)
1. Would the Google Trends API alpha grant access for this use case, and what quota and terms come with it?
2. Can a point-in-time product-to-firm mapping (which product is "most popular" as of a date) be built without look-ahead? S2 needed Nielsen TV-ad data plus hand-chosen queries.
3. Does the Teoh et al. (AOS 2023) revenue-management study add a post-2008 test of product-search revenue nowcasting?

## Sources
Retrieved 2026-09-26.
- **S1** Da, Engelberg & Gao (2011), "In Search of Attention", *Journal of Finance* 66(5): 1461–1499.
  - Citation: https://ideas.repec.org/a/bla/jfinan/v66y2011i5p1461-1499.html (no abstract on the page). Abstract via search results.
  - Tier 1. Supportive.
- **S2** Da, Engelberg & Gao, "In Search of Earnings Predictability", working paper, draft of April 9, 2010 (first draft October 19, 2009; "Preliminary and Incomplete").
  - https://care-mendoza.nd.edu/assets/152190/engelberg.pdf. Full text supplied by the coordinator in pass 2 as a markdown conversion; no page numbers, so section and table numbers are given.
  - Tier 1 WP. Supportive.
- **S3** Bijl, Kringhaug, Molnár & Sandvik (2016), "Google searches and stock returns", *International Review of Financial Analysis* 45: 150–156.
  - https://econpapers.repec.org/RePEc:eee:finana:v:45:y:2016:i:c:p:150-156 (abstract verbatim).
  - Tier 1. **Disconfirming.**
- **S4** Google, "FAQ about Google Trends data", Trends Help.
  - https://support.google.com/trends/answer/4365533?hl=en
  - Tier 1 (vendor doc). **Disconfirming (instability).**
- **S5** Google Terms of Service, "Effective July 30, 2026".
  - https://policies.google.com/terms?hl=en-US
  - Tier 1 (primary). Neutral.
- **S6** trends.google.com robots.txt.
  - https://trends.google.com/robots.txt
  - Tier 1 (primary). Neutral.
- **S7** Google Trends API (alpha) page.
  - https://developers.google.com/search/apis/trends
  - Tier 1 (vendor doc). Neutral.
- **S8** Challet & Bel Hadj Ayed (2014), "Do Google Trend data contain more predictability than price returns?", arXiv:1403.1715.
  - https://arxiv.org/abs/1403.1715. Full text supplied by the coordinator in pass 2; section and table numbers are given.
  - Tier 1 WP. **Disconfirming.**
- **S9** Lind & Ramesh (2025), "Using internet search data to predict aggregate retail sales and enhance firm-level revenue expectations", *Contemporary Accounting Research* 42(3): 1557–1588.
  - https://onlinelibrary.wiley.com/doi/10.1111/1911-3846.13043 (403). Abstract via search snippet.
  - Summary: https://business.rice.edu/wisdom/forecasting-retail-sales-just-got-smarter-thanks-google-searches (Tier 3; used only for the flagged return figures).
  - Tier 1 for the abstract claims. Supportive.
- **S10** Cebrián & Domenech (2024), "Addressing Google Trends inconsistencies", *Technological Forecasting and Social Change* 202, doi:10.1016/j.techfore.2024.123318.
  - https://ideas.repec.org/a/eee/tefoso/v202y2024ics0040162524001148.html (abstract verbatim).
  - Tier 1. **Disconfirming (instability).**
- **S11** Eichenauer, Indergand, Martínez & Sax (2022), "Obtaining consistent time series from Google Trends", *Economic Inquiry* 60(2): 694–705.
  - https://econpapers.repec.org/article/blaecinqu/v_3a60_3ay_3a2022_3ai_3a2_3ap_3a694-705.htm
  - Tier 1. **Disconfirming (instability).**
- **S12** Gummer & Oehrlein (2025), "Using Google Trends Data to Study High-Frequency Search Terms: Evidence for a Reliability-Frequency Continuum", *Social Science Computer Review*, doi:10.1177/08944393241279421 (403; search snippets).
  - Tier 1. **Disconfirming (instability).**

## Pass 2 note
The coordinator supplied full texts for S2 and S8. No new sources and no new searches were used; cumulative use stays at 12 sources and 19 searches.

**Corrected from pass 1:**
- S2's universe is 865 TV-advertising public firms, skewed large, not Russell 3000.
- The "few products / growth / earnings management" split attributed to S2 is not in the April 2010 draft.

**Added:**
- S2: table-level figures, including the announcement-return magnitude (≈ 20 bps per s.d. over 3 days, in-sample, gross) and the analyst-surprise dispersion split. Its description of S1 as concentrated in small stocks.
- S8: universe (S&P 100), cost level (2 bps), Table 2 results, the flat post-2011 out-of-sample rule, and the data-revision and rescaling evidence.

**Verdict change:** none.
- Claim A stays NOT SUPPORTED. S8's text strengthens the "no incremental value" reading and adds a flat out-of-sample index result.
- Claim B stays INSUFFICIENT. S2 is still a single in-sample, gross return result.
- The recommendation is unchanged.
