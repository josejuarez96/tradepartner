# Research Report: Lazy Prices, post-publication and net of costs

**Brief:** #663  ·  **Date:** 2026-10-03  ·  **Status:** COMPLETE (revised once). All four done-when items are delivered within budget: 14 of 15 sources (the S6 PDF was added in revision), 23 of 30 searches, and 12 fetch attempts by this agent, of which 5 were blocked (HTTP 403) and 3 returned unreadable PDFs, plus 2 firecrawl fetches by the coordinator. The most important disconfirming source (Cakici, Tang, Zaremba 2025) could be read only at abstract level. See the UNVERIFIED items.  ·  **Agent/model:** research agent (claude-opus-5-5)

Predecessor: [2026-10-03-disclosure-change-evidence-review.md](2026-10-03-disclosure-change-evidence-review.md). S-numbers follow it. New sources are S60 onward.

## Answer
**Verdict:** 10-K: **MIXED**  ·  10-Q: **INSUFFICIENT**  ·  **Confidence:** low for 10-K; medium that 10-Q is INSUFFICIENT.

Our setting is long-only, ADR 0006 top-1,000 universe, monthly rebalance, net of costs. **No Tier 1 or Tier 2 source tests Lazy Prices in that setting.** No source anywhere reports it net of costs.

**Evidence for the effect:**
- The original paper (S1) reports value-weighted long-short alphas of 34–58 bp/month. It covers all CRSP firms, 1995–2014, with 10-Ks and 10-Qs pooled and a 3-month hold.
- Its long "non-changer" leg carries roughly 57–78% of the factor-model alpha.

**Evidence against it, in or near our setting:**
- **S4 (Tier 1)** uses a >$300M universe and 10-Ks, 1999–2018. Its deterministic bag-of-words version of the signal earns **no significant alpha**.
- **S60, Cakici, Tang, Zaremba 2025 (Tier 1 working paper; abstract only)** finds that similarity-based predictability is confined to microcaps, low-attention and hard-to-trade stocks, and "fades over time".
- **S61, Frömel et al. 2023 (Tier 1)** finds no effect in REITs and only a small one in financials.

**Evidence added in revision, S6 (S&P Global, Tier 2, full PDF):**
- A positive result on an **equal-weighted Russell 3000**, Jan 2008–Dec 2020, gross of costs, with 10-K and 10-Q sections pooled.
- Its long leg alone earned about **1.75–1.77%/yr** above the market, which is **33–42%** of its long-short spread (Exhibit 1).
- The period runs partly after S1's first circulation (SSRN, Aug 2010) and after S1's sample (2015–2020). S6 does not split those years out, and it is neither large-cap, nor value-weighted, nor net.

10-K therefore has conflicting Tier 1 evidence: MIXED, with the post-publication evidence pointing against. 10-Q has no verified separate result in any source: INSUFFICIENT. **S6 changes neither grade** (see the Grades table).

**How much the 10-K grade leans on an abstract.** The "against" side of the MIXED grade rests most heavily on S60, read **at abstract level only**: the PDF was unreachable both here and through the coordinator's firecrawl attempt. **Without S60 the 10-K grade would still be MIXED**, but in-sample only, with no post-publication evidence against:
- *For:* S1, S4's reproduction of the Lazy Prices 10-K portfolio, and S6.
- *Against:* S4's own deterministic bag-of-words 10-K null in a >$300M universe, and S61's industry null.

It would still not be SUPPORTED, because there is no large-cap post-publication test and no net-of-cost test.

**Evidence-implied option (done-when item 3; the owner decides):**
Build only B5 (fundamentals target): no Tier 1/2 source supports B6's return claim long-only, large-cap, post-publication and net; the only post-publication Tier 1 signal (S60, abstract) puts the effect in microcaps and shows it fading; and S6's positive post-2008 result is equal-weighted Russell 3000, gross.

S6 does not change this option. B6 stays a cheap later add-on to B5's corpus, with its prior set near zero, not at the published magnitude.

## Done-when item 2: long-leg share and VW / large-cap results (S1, NBER w25084)

| Quantity | Figure (quoted or read from the table) | Where |
|---|---|---|
| VW long-short (Q5−Q1) | "34-58 basis points per month — up to 7% per year (t=3.59) - in value-weighted abnormal returns" | p.4 (intro); p.17 ("up to 58 basis points per month (t=3.59)") |
| VW 5-factor alphas, measure block 1 (cosine/Jaccard) | Q1 −0.12 (t=−1.38); Q5 **0.23 (t=2.23)**; Q5−Q1 **0.34 (t=2.53)** | Table II Panel B. The markdown conversion duplicated rows, so which measure each value belongs to is **UNVERIFIED** |
| VW 5-factor alphas, measure block 2 (min-edit/simple) | Q1 −0.17 (t=−2.02); Q5 **0.21 (t=1.84)**; Q5−Q1 **0.37 (t=2.45)** | Table II Panel B (same caveat) |
| **VW long-leg share** | 0.23/0.34 ≈ **68%**; 0.21/0.37 ≈ **57%** | computed from the rows above |
| EW 5-factor alphas, Q5 / Q5−Q1 | Cosine 0.21 (3.28) / 0.32 (4.21); Jaccard 0.28 (3.57) / 0.42 (4.31); MinEdit 0.30 (4.11) / 0.45 (5.46); Simple 0.21 (2.68) / 0.27 (3.01) | Table II Panel A |
| **EW long-leg share** | 66%, 67%, 67%, 78% | computed |
| Event-time legs | "any positive alpha on the Q5 long side ... quickly reverts to zero, while the negative alpha persists and increases up to 6 months out" | p.18, Figure 7 (market-adjusted CARs, Jaccard) |
| Size of firms in the legs | Mean market value: Q1 (changers) $3.51B; Q5 (non-changers) $2.46B | Table III |
| Size splits | **None in the NBER version.** "Exist in large firms" (p.28–29) rests on VW returns and Table III, not on a size-sorted test | whole text and tables searched |
| Panel B mapping, rechecked in revision | The extracted Panel B prints the 3-factor and 5-factor rows of each two-measure block twice, identically, and the "58 bp (t=3.59)" figure from the text appears nowhere in the extracted table. The mapping stays **UNVERIFIED** | S1 file, Table II, raw lines |
| S6 long leg vs long-short (EW Russell 3000, 2008–2020; Tier 2) | MD&A: long-market 1.77%/yr (p=0.018) vs long-short 4.18%/yr → **42%**. Risk Factors: 1.75%/yr (p=0.004) vs 5.26%/yr → **33%** | S6 Exhibit 1, columns [6] and [9] |
| 10-K only, VW (as reproduced by S4) | Lazy Prices 10-K, cosine, annualized: Q5 VW 5-factor alpha **5.16% (t=2.81)**; Q5−Q1 **8.16% (t=3.77)** → long share ≈ **63%**. EW: Q5 2.76% (2.7); Q5−Q1 2.28% (2.24) | S4 Table 4. **Which S1 table S4 took these from is not stated (UNVERIFIED)** |

**How to read the long-leg share for our setting** (*inference, not evidence*):
- The Q5 alpha is measured against a 5-factor model that includes long-short SMB, HML, UMD and liquidity factors. A long-only holder benchmarked to the market or the universe earns the Q5 alpha only after those exposures, and S1 does not report Q5 minus the universe.
- S1's event-time evidence (Figure 7) says the long-side alpha decays quickly while the short side persists. The calendar-time share of 57–78% is therefore an upper bound on what a long-only book keeps.

## Evidence

| Claim | Source | Tier | Key figure (quoted) | OOS / post-pub? | Net of costs? |
|---|---|---|---|---|---|
| Non-changers beat changers (10-K and 10-Q pooled) | S1 Cohen, Malloy, Nguyen, NBER w25084 (Sept 2018, rev. March 2019); JF 75(3):1371–1415 (2020) | 1 | VW "up to 58 basis points per month (t=3.59)" p.17; EW L/S "18-45 basis points per month" p.17 | No. The sample is 1995–2014 (p.11). The SSRN version was posted 14 Aug 2010 (search snippet, **UNVERIFIED**), so 2010–2014 is post-SSRN but not split out | **No.** Argument only: "very modest turnover" and "does not appear to be a function of transaction costs" (p.6); no net returns computed |
| Risk Factors section changes are the most informative | S1 | 1 | "up to 188 basis points per month (t=2.76)" (p.22, Table VII Panel A). The table's Risk Factors row is printed as 0.01–0.02 while the note says returns are ×100, so **the units are inconsistent (UNVERIFIED)**. Appendix A-1 (post-SOX 2003–2014, Risk Factors): EW 5-factor up to 1.11 (t=3.06); VW 1.01 (t=2.46) | No | No |
| MD&A changes predict returns, less strongly | S1 | 1 | "ranging between 11-22 basis per month" (p.22). VW 5-factor in Table VII Panel B: 0.22 (t=1.42), 0.33 (**), 0.33 (t=1.97), 0.25 (t=1.42), so 2 of 4 measures are insignificant | No | No |
| 10-K-only and 10-Q-only results are "similar" to pooled | S1, fn 18 (p.16), Appendix Table A-13 | 1 | Text claim only; **A-13 is not in the NBER file (UNVERIFIED)** | No | No |
| Semantic (Doc2Vec) 10-K non-changers earn alpha; **bag-of-words version does not** | S4 Adosoglou, Lombardo, Pardalos, ESWA 164 (2021) 114053 (accepted postprint) | 1 | Universe: market cap >$300M, 10-K only, 1999–2018, annual rebalance (§3.2, §4). BoW (cosine >0.95): VW 5-factor **5.1%/yr (t=1.54)**, EW 3.57 (t=1.30), not significant (Table 2). PV-DM: VW 5-factor 5.87 (t=1.59), not significant; EW 7.45 (t=2.69) (Table 1). With a momentum filter (NSP): VW 5-factor 8.45 (t=2.61) (Table 2) | Partly. 2015–2018 is post-S1 but not split out; overlapping sample | **No.** It only asserts "lower transaction costs compared to Lazy Prices" (§4.3) |
| Practitioner: high-similarity firms outperform; the long side contributes | S6 S&P Global MI (F. Zhao), "U.S. Filings: No News is Good News", May 2021 (full PDF) | 2 | **Design** (A.4; §2; §3):<br>- Russell 3000, Jan 2008–Dec 2020; equal-weighted quintiles; month-end rebalance; one-month forward horizon.<br>- The latest score in a 6-month look-back window. **10-K and 10-Q pooled**: year-over-year similarity of 5 sections common to both forms (A.1).<br>- TF-IDF cosine. Returns are "Carhart Four-Factor Adjusted".<br>**Results** (Exhibit 1):<br>- MD&A: long-short 4.18%/yr (p=0.000); long-market 1.77%/yr (p=0.018); 476 firms per quintile.<br>- Risk Factors: long-short 5.26%/yr (p=0.000); long-market 1.75%/yr (p=0.004; hit-rate p=0.230); 448 firms per quintile.<br>- Composite of Risk Factors, QQDMR and Controls: long-market 2.37%/yr (Exhibit 2).<br>**Robustness:**<br>- After 8 strategy controls: 4.00% (t=3.39) and 4.88% (t=4.55) (Exhibit 4); sector-neutral 2.86% and 3.92% (fn 23).<br>- No cap-weighted or size split. The long-short spread correlates −0.40 and −0.39 with the small-cap effect (Exhibit 3), so the long side tilts large. | **Partly.** 2008–2020 overlaps S1's sample (1995–2014) for 2008–2014. It runs after S1's SSRN posting (Aug 2010, **UNVERIFIED**) from Aug 2010, and after S1's sample from 2015–2020. It ends 6 months after JF publication (June 2020). None of these sub-periods is reported separately | **No.** There is no cost or turnover analysis, only the boilerplate "Such costs would lower performance" (Exhibit notes) |
| Return predictability from similarity is confined to microcaps and low-attention, hard-to-trade stocks, and fades over time | S60 Cakici, Tang, Zaremba, "Between the Lines: Textual Features in Financial Reports and Expected Stock Returns", SSRN 5224055 (21 Apr 2025) | 1 (working paper; abstract only) | Abstract (search snippet): predictability "arises solely from similarity measures and occurs in specific market segments, such as microcaps, firms that receive little investor attention, and overpriced securities"; it "fades over time and prevails mainly in hard-to-trade stocks". A Tier 3 relay (QuantSeeker) says "40 standard text-based features ... derived from a bag-of-words approach" | Yes: it reports decay over time (sample **UNVERIFIED**) | Implied by "hard-to-trade"; figures **UNVERIFIED** |
| No textual-uniqueness effect in REITs; small in financials | S61 Frömel, Kolmeder, Wagner, *Finance Research Letters* 53 (2023) 103601 | 1 | Abstract (RePEc): "no significant impact of textual uniqueness on future returns" for REITs; for financials the effect is "albeit small, apparent"; effects "cannot be readily generalized and may be dependent on the observed industry" | Sample **UNVERIFIED** | Not stated |
| Positive-language similarity has the **opposite** sign in a top-1,000 universe | S3 Padyšák, SSRN 3690461 (10 Sept 2020); read via the Quantpedia relay plus the SSRN abstract | 1 paper; numbers via a Tier 3 relay | Relay: "approximately the largest 1000 US stocks", Feb 2007–May 2020, 1-month hold; decile 1 1.14%/month vs decile 10 0.73%/month (= the "0.41% monthly" spread; relay Table 1); bottom−top 5.47%/yr, volatility 6.48% (relay Table 2). For uncertainty-language similarity the relay says the Lazy Prices direction holds | Partly post-S1-sample | No |
| Lazy Prices is **not** in the Chen–Zimmermann Open Source Asset Pricing set | S62 OSAP SignalDoc.csv (GitHub, OpenSourceAP/CrossSection), plus the home page (October 2025 release) | 1 (dataset documentation) | 331 signal rows. No row cites Cohen–Malloy–Nguyen or any filing-text similarity signal (grep for lazy, textual, filing, 10-K, Malloy, Nguyen) | n/a: **no OSAP post-sample or post-publication series exists** | n/a |
| Average anomaly decay | McLean & Pontiff (2016), via search snippet; register QI-2 | 1 | Anomaly returns "about 26% lower out-of-sample and about 58% lower after publication" | Context, not this signal | No |

**Tier 3, test design only (not evidence):**
- **S7 (iqueipopg/lazy-prices).** Universe: the current S&P 100, so it carries survivorship bias. Data: 10-K only, FY2008–FY2025. Method: TF-IDF cosine and Jaccard on the full document, Item 1A and Item 7; equal-weighted; 12-month hold; 10 bp cost per unit turnover.
  - Result: L/S −0.92%/yr gross, FF5+MOM −0.93% (t=−0.49).
  - Every quintile has a positive alpha (2–8%/yr), the survivorship signature.
  - Full-document cosine has a median of **0.996** (IQR 0.994–0.998), i.e. it is saturated in large caps. Item 7 extraction fails for 23% of pairs.
  - Deflated Sharpe ratio across 54 variants: 0.13.
- **S63 QuantConnect, "Filing Language Stability as a Selection Signal".**
  - Setup: the 100 most liquid US stocks, Jan 2020–Jun 2026, long-only top 25 by Brain similarity, monthly, weights optimized for Sharpe.
  - Result: Sharpe 0.558 vs SPY 0.533; 9 of 25 parameter combinations beat SPY; no cost model stated.

## Grades (§6.1, our setting: long-only, ADR 0006 universe, monthly, net)

| Claim | Grade | Why |
|---|---|---|
| **10-K** change → lower returns, usable long-only in large caps, post-publication, net | **MIXED** (low confidence) | 1. *For:* S1 (VW, pooled) and S4's reproduction of the Lazy Prices 10-K portfolio (VW Q5 5.16%/yr) are positive and in-sample. S6 (Tier 2) is positive on an EW Russell 3000, 2008–2020, gross, with 10-K and 10-Q pooled.<br>2. *Against:* S4's own deterministic BoW 10-K signal is insignificant in a >$300M universe. S60 says the effect is confined to microcaps and hard-to-trade stocks and fades over time. S61 finds an industry-dependent null.<br>3. *Missing:* no positive post-publication Tier 1/2 test and no net-of-cost test.<br>Conflicting evidence plus a microcap-confinement claim meets §6.1 MIXED. It is not NOT SUPPORTED, because S60 is one source read at abstract level.<br>**Revision check:** S6 is a "for" source with a partly post-circulation period. It is equal-weighted, not large-cap, and not net, so it does not resolve the conflict. **The grade is unchanged.** Without S60 the grade is still MIXED (see the Answer). |
| **10-Q** change → lower returns, same setting | **INSUFFICIENT** | No source gives a verified 10-Q-only result. S1's A-13 is cited but absent from the NBER file; S6 pools sections common to 10-K and 10-Q. That is fewer than 2 qualifying sources.<br>**Revision check:** S6's full PDF confirms the pooling: it takes the latest 10-K or 10-Q score in a 6-month window (A.4). **The grade is unchanged.** |

## Disconfirmation
- **Searches run** (all logged below): failed replications (#1, #19, #21); post-publication decay (#2, #13); large-cap nulls (#3, #17); weaker recent periods (#4, #9); OSAP coverage (#5); cost implementation (#6); size subsamples in S1 (#15, #20).
- **What was found against:**
  - S60: microcap-confined, fading, hard-to-trade (Tier 1; abstract only).
  - S4: BoW 10-K null in a >$300M universe (Tier 1, full text).
  - S61: REIT null and small effect in financials (Tier 1; abstract).
  - S1 itself: the long-leg alpha "quickly reverts to zero" (Figure 7); VW MD&A alphas are insignificant on 2 of 4 measures (Table VII Panel B); the Risk Factors headline has a units inconsistency.
  - OSAP excludes the signal, so there is no independent post-publication series to check.
  - S6 (revision), against our setting in particular:
    - Its long leg captures only 33–42% of the long-short spread (Exhibit 1).
    - The Risk Factors long leg's monthly hit rate is not significant (p=0.230).
    - It is equal-weighted across the Russell 3000, so small caps drive it.
    - Auxiliary sections lack dispersion because "many filers have the textual similarity score of 1" (§3.2). This matches S7's saturation finding.
  - Tier 3 only: an S&P 100 null (S7) and a top-100 near-null (S63).
- **Nothing found:** any Tier 1/2 test of Lazy Prices restricted to large caps after 2014 with positive results; any net-of-cost test; any 10-Q-only result.

## Caveats & gaps
- **Table II Panel B (VW) of S1 was corrupted in the markdown conversion** (duplicated rows). The VW Q5 and L/S figures are paired correctly within each block, but which measure each belongs to is unverified. Reading Panel B from a clean PDF is the single most useful check.
- S1 pools 10-K and 10-Q and holds for 3 months, not 1. S4 states that Lazy Prices holds for "9 months" (§4.3), which conflicts with S1's 3 months (Table II note; p.16). S4 is likely wrong.
- S1 reports no size-sorted test. "Large caps" is inferred from VW returns and mean market cap. A mean of $2.5–3.5B (Table III) sits well below a top-1,000 cutoff's typical constituent; that comparison is *inference*.
- S60 drives the 10-K grade toward MIXED, and only its abstract was read. If the full text shows a positive large-cap result, the 10-K grade could move up. If it shows a large-cap null after a stated date, the grade becomes NOT SUPPORTED.
- *Inference, not evidence:* apply the McLean–Pontiff haircut (−58%) to S1's VW L/S of 34–58 bp and the long share of about 60%. The result is a gross factor-adjusted long-only edge of roughly 9–15 bp/month before costs, and before any extra large-cap shrinkage.
- **Test design lessons from S7** (Tier 3, design only):
  - Full-document cosine saturates in large caps, so prefer section-level, Jaccard or sentence-diff measures.
  - Item 7 is incorporated by reference for many banks.
  - The universe must be point-in-time, as ADR 0006 already requires.
  - Count trials and use deflated Sharpe ratios.

## UNVERIFIED items
- S1: the measure-to-row mapping in Table II Panel B; Appendix A-13 (10-K-only and 10-Q-only figures); the units of the Risk Factors row in Table VII; the meaning of "Monthly turnover" in Table III (0.0663–0.0867; probably stock share turnover, not portfolio turnover); the SSRN posting date of 14 Aug 2010 (search snippet; SSRN returned 403).
- S4 Table 4: which S1 table it reproduces, and whether that table is the JF version's.
- S6 (resolved in revision: universe, period, weighting, costs). Still open: whether Exhibit 1 column [6] is market-adjusted (A.5) or Carhart-adjusted (the exhibit note), since the two statements conflict. S6 also reports no turnover.
- S60: sample, universe, the actual similarity alpha by size, and the timing of the decay (abstract only).
- S61: sample and measure.
- S3: every figure (relay only; the PDF was unreachable).
- McLean–Pontiff 26% / 58% (search snippet; already in register QI-2).

## Follow-up questions (not answered here)
- Does S1's JF version (or its Internet Appendix) contain a size-sorted or NYSE-breakpoint table?
- Does the S1 change → future-earnings result (Table IX), which bears on B5's prior, hold in large caps?
- Does Padyšák's sign flip for positive language replicate in the post-2020 period?

## Search log (23 of 30 searches; 12 fetch attempts)
| # | Query (abridged) | Purpose | Result |
|---|---|---|---|
| 1 | "Lazy Prices" replication post-publication returns 10-K similarity | disconfirm: replication | S7 (Tier 3), a Tier 3 vendor case study; no Tier 1/2 |
| 2 | "Lazy Prices" anomaly decay after publication out-of-sample 2015-2023 | disconfirm: decay | general decay literature only |
| 3 | "Lazy Prices" large-cap S&P 500 no effect | disconfirm: large-cap null | Tier 3 QuantConnect only |
| 4 | 10-K similarity replication weaker recent period | disconfirm | S43 (out of scope), Brown–Tucker decline |
| 5 | Chen Zimmermann OSAP text signals 10-K similarity | OSAP coverage | nothing; settled by grep of SignalDoc.csv |
| 6 | "Lazy Prices" transaction costs net returns | costs | nothing relevant |
| 7–8 | Lazy Prices SSRN 1658471 posting date | publication date | 14 Aug 2010 (snippet) |
| 9 | textual changes post-2014 Lazy Prices robustness large firms | disconfirm | **found S60** |
| 10 | Cakici Tang Zaremba "Between the Lines" | S60 detail | abstract-level only |
| 11 | S&P Global "No News is Good News" universe and period | S6 design | not found |
| 12 | S&P "Hiding in Plain Sight" Yang Oyeniyi | S6 predecessor | not found |
| 13 | text-based anomalies post-publication McLean Pontiff | disconfirm: decay | MP 26% / 58%; nothing text-specific |
| 14 | Alpha Architect "Lazy Prices" | Tier 2 | 2016 summary only (pre-publication) |
| 15 | "Lazy Prices" Kent Daniel discussion size microcaps | size split | slides found, unreadable |
| 16 | Cakici et al. journal version | S60 | not found |
| 17 | non-changers long-only large-cap Russell 1000 | disconfirm: large-cap | Tier 3 QuantConnect |
| 18 | Brain Language Metrics similarity backtest large caps | top-1,000 data | S3 relay; QuantConnect |
| 19 | 10-K similarity insignificant after 2014 / disappeared | disconfirm | only S7 |
| 20 | JF Internet Appendix size terciles | size split | not found |
| 21 | "we replicate" Lazy Prices extended sample | disconfirm: replication | **found S61** |
| 22 | "Where prices are not lazy" abstract | S61 | RePEc abstract read |
| 23 | "Between the Lines" pdf | S60 full text | not found |

**Fetches:**
- Read: Harvard DASH, 7 Mar 2019 draft (fetch summary; it confirmed that no size table exists and that A-13 is referenced); QuantSeeker; QuantConnect; RePEc for S61.
- Blocked (403): SSRN 5224055, SSRN 1658471, the S&P PDF, ScienceDirect for S61.
- Unreadable PDFs: arXiv 2606.29290, Kent Daniel's slides, the Ivey copy of Lazy Prices.

**Coordinator firecrawl fetches (revision):**
- S6 PDF: **ok**, read in full, so S6 now counts as a full-text source (14 of 15).
- S60 PDF and SSRN landing page: **blocked or landing page only**. S60 stays at abstract level.

Pre-fetched full texts, read locally: S1, S4, S6 (page), S7, S3 relay and SSRN abstract, OSAP home and SignalDoc.csv, Quantpedia strategy page (nothing usable).

## Sources
- S1 Cohen, Malloy, Nguyen, "Lazy Prices", NBER w25084 (Sept 2018, rev. March 2019): https://www.nber.org/papers/w25084 ; JF 75(3):1371–1415 (2020): https://onlinelibrary.wiley.com/doi/10.1111/jofi.12885 ; DASH draft: https://dash.harvard.edu/server/api/core/bitstreams/48b75045-2a9a-463a-b168-f41ca002078c/content ; SSRN: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1658471 (Tier 1)
- S3 Padyšák, SSRN 3690461: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3690461 ; relay: https://quantpedia.com/the-positive-similarity-of-company-filings-and-the-cross-section-of-stock-returns/ (Tier 1 paper; Tier 3 relay)
- S4 Adosoglou, Lombardo, Pardalos, ESWA 164 (2021) 114053, postprint: https://air.unipr.it/handle/11381/2887410 (Tier 1)
- S6 S&P Global MI, "U.S. Filings: No News is Good News" (4 May 2021): https://www.spglobal.com/market-intelligence/en/news-insights/research/us-filings-no-news-is-good-news ; PDF (read via the coordinator's firecrawl): https://www.spglobal.com/content/dam/spglobal/mi/en/documents/general/us-filings-no-news-is-good-news.pdf (Tier 2)
- S7 iqueipopg/lazy-prices: https://github.com/iqueipopg/lazy-prices (Tier 3, design only)
- S60 Cakici, Tang, Zaremba, SSRN 5224055 (2025): https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5224055 ; relay: https://www.quantseeker.com/p/weekly-research-recap-b6d (Tier 1 working paper, abstract only; Tier 3 relay)
- S61 Frömel, Kolmeder, Wagner, FRL 53 (2023) 103601: https://ideas.repec.org/a/eee/finlet/v53y2023ics1544612322007772.html (Tier 1)
- S62 Chen & Zimmermann, Open Source Asset Pricing: https://www.openassetpricing.com/ ; https://github.com/OpenSourceAP/CrossSection ; paper: https://papers.ssrn.com/abstract=3604626 (Tier 1)
- S63 QuantConnect, "Filing Language Stability as a Selection Signal": https://www.quantconnect.com/research/20966/filing-language-stability-as-a-selection-signal/ (Tier 3, design only)
- McLean & Pontiff (2016), via search snippet: https://www.researchgate.net/publication/254926004_Does_Academic_Research_Destroy_Stock_Return_Predictability (Tier 1; register QI-2)
- Internal: [ADR 0006](../decisions/0006-universe-and-cadence.md); [hypothesis backlog B5/B6](hypothesis-backlog.md); [handoff §6.1](2026-09-24-initial-research-handoff.md)
