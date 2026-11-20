# Research Report: SEC-filing events searched for the 200 bp shape (gross ≥ 2 × round trip, n_eff ≥ 255 / 765, long side, tradable band)

**Brief:** #3687  ·  **Date:** 2026-11-20  ·  **Status:** INCOMPLETE. The verdict and the map are complete for every candidate in the brief. What is missing is verification: every literature figure below was read from search-result summaries or abstracts only, because page fetches to ideas.repec.org, arxiv.org and clsbluesky.law.columbia.edu failed in this session (host not resolvable). So every external figure is marked UNVERIFIED. No store was opened, no return was read, nothing was registered  ·  **Agent/model:** team shapefile. Budget: the brief as relayed to this team gave the question, the decision and the scope but no budget, so the team set its own: about 35 searches and fetches. It used 33 searches and 3 failed fetches.

## Answer
**Verdict:** NOT SUPPORTED that any SEC-filing event clears both lines on published evidence. To clear, an event needs a long-side gross ≥ 200 bp per event from the next open, in names of ≥ $10M daily dollar volume, and n_eff ≥ 255 (20 sessions) or ≥ 765 (60 sessions) over 2019-2023. **None does.** Two candidates come close, each failing a different line. Officer/director purchase clusters in $10-200M names probably clear the count but probably not the gross. Cash merger arbitrage fails the 200 bp line, but it does not have the shape the line assumes.  ·  **Confidence:** medium on the "none clears both" verdict, because the failures are mostly large and point the same way. Low on every gross figure, which are UNVERIFIED summaries. Low on every event count not taken from the store, which are assumptions and labelled as such.

1. **Most events with a large published drift after the filing are short-side events in names we cannot trade.** Going-concern opinions show −14 % over a year. Bankruptcy filings show at least −12.5 % over six months. Late-filing (NT) notices, auditor resignations and delisting notices also drift down. They are short-only, concentrated in micro and nano caps, and often have no borrow. They are recorded and ranked last, as the brief requires.
2. **The classic long-side drifts were large before 2003 and are gone or unproven since.** Buyback authorisations went from positive to nothing for 2003-2012 events. Equity offerings went from negative to nothing over the same years (Fu and Huang, *Management Science* 2016, 14,309 buybacks and 6,739 offerings). The dividend-initiation drift does not survive value weighting (Boehme and Sorescu, *JF* 2002). The outperformance of firms emerging from Chapter 11 has a median of zero and reportedly disappeared in the 2010s. Index deletions averaged −0.6 % in 2010-2020 (Greenwood and Sammon, NBER).
3. **The idea that comes closest on count: officer/director purchase clusters, held 60 sessions, in $10-200M names.** The store already holds the data. On assumed cluster shares, 2019-2023 gives about 420 to 2,400 effective events (middle about 1,000) against the 765 needed. The gross is the problem. A 2003-2022 study reports +3.4 % over 60 days for ordinary insider purchases, but about +2 % of that comes in the first five days after the filing. Our own register's G2-5 says that in $30-500M firms, skipping day 1 leaves no significant alpha. From the next open, a plausible gross is 50 to 200 bp. **Pass chance under the E1-shape rule: about 18 to 20 %. More than half of that is a pass on a true net edge at or below zero. The chance of a pass on a real 200 bp edge is about 5 to 7 %.**
4. **The second closest: cash merger arbitrage (target held after day one).** The published per-deal return is about 1.5 % gross over 2000-2020, or about 100 bp over T-bills (a practitioner study, UNVERIFIED). That is under the 200 bp line. But the 200 bp line assumes a full round trip and a 1,500 bp spread sd per event, and arbitrage has neither. A completed cash deal costs one side, and the payoff sd is about 900 bp. On its own arithmetic the deal needs about 176 bp over T-bills for 80 % power at n_eff 200, so it still misses. **Pass chance at the event-study rule: about 30 %, much of it on a near-zero net edge. Under line 40 as written (excess over SPY), the counted trial is close to zero, because a cash-like book cannot beat SPY in a rising market (PR #3683, ambiguity 1).**
5. **Activist 13Ds and spin-off children fail on count, not on story.** Activist 13Ds in names of ≥ $10M a day come to about 40 to 100 a year (UNVERIFIED), so about 70 to 250 effective events over five years against 765. The published drift after the filing is weak: no reversal, but no clear continuation either. Spin-off children come to about 20 to 40 a year in band, with 12-month holds and a very wide sd, so about 25 to 100 effective events. The 200 bp shape does not change C2's or C3's verdicts.
6. **Where the 200 bp shape does change a prior verdict.** B7 died on count in the ≥ $200M band (47 events), and IC-B1 died on a gross of 0.13 %/month for all purchases over 21 sessions. Neither death applies to a re-scoped card: clusters only, 60 sessions, $10-200M. That card's binding question moves from count to gross, and the register's own G2-5 (S3) speaks against the gross in exactly that band. IC-B3, C2, C3, C1 and FC-6 are unchanged.

**One line:** nothing found that clears both lines. The nearest miss is insider purchase clusters in $10-200M names (count probably passes, gross probably fails). The second is cash merger arbitrage (gross about half the bar, and the wrong yardstick under line 40). The cheapest informative next check is a counts-only query on the Form 4 table the store already holds.

## The map (every candidate, one table)

Lines: gross per event from the next open ≥ 200 bp, and n_eff ≥ 255 (20-session hold) or ≥ 765 (60-session hold), at the PR #3683 sd assumptions (866 / 1,500 bp). Raw-to-n_eff divisor B = 2 to 4 (same-date clustering and hold overlap) unless stated. 5y = 2019-2023, 7y = 2017-2023. Bands are 20-session median daily dollar volume. "Assumed" counts are this report's assumptions, not store reads.

| # | Candidate | Side | Gross from next open (published; horizon) | Post-2015 grade | Where it lives | Raw in-band events/yr | n_eff 5y / 7y | vs 200 bp | vs 255 / 765 | Store today |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Officer/director purchase **clusters**, $10M+ | long | 50 to 200 bp plausible (≈ +1.4 to +3.4 % over 60 d all sizes, minus front-loading) | MIXED | Strongest in small firms; G2-5 S3 against in $30-500M cap | 335 to 970 (assumed cluster share 15 to 30 % of 2,240 to 3,220 o/d purchase issuer-days a year) | 420 to 2,400 (mid ≈ 1,000) / 590 to 3,380 (mid ≈ 1,400) | **below, probably** | **clears 765, probably** | **held** (`sec_form4_transactions`) |
| 2 | Cash merger arbitrage (target after day 1) | long | ≈ 1.5 % raw per deal, ≈ 100 bp over T-bills; about 129 days | MIXED | Unknown by size; spreads down by more than 400 bp since 2002 | 45 to 65 (Form 25 at ≥ $10M is about 90 a year from 2020-08, all reasons, × a 50 to 70 % cash share) | 110 to 270 (B 1.2 to 2) / 5y plus very few (Form 25 thin before 2019) | **below** (own-shape bar ≈ 176 bp) | near 255, below 765 | Form 25 held; deal filings and terms not held |
| 3 | Activist 13D | long | No clear drift after the window; [−20, +20] ≈ 7 % mostly before filing | MIXED (prior C3), decline in newer samples | Median 13D target about $2M a day (C3 check) | 40 to 100 (assumed) | 70 to 250 / 95 to 350 | unknown, likely below | **below** | adapter, 0 rows |
| 4 | Spin-off children (parents weaker) | long | Positive 2001-2013, magnitude UNVERIFIED; 12 to 36 months | MIXED (prior C2) | Small children | 20 to 40 (C2: 30 to 60 total) | 25 to 100 / 35 to 140 (12-month overlap) | possibly above | **below** | 10-12B in forms list; 76 spin-off rows from 2020-11 |
| 5 | Issuer tender / Dutch auction (SC TO-I) | long | +1.74 % buy-after-announcement to tender (1980s) | INSUFFICIENT | n/a | 15 to 40 operating-company offers (assumed; many SC TO-Is are funds) | 25 to 100 | below | **below** | not held |
| 6 | Large buyback authorisations | long | ≈ 0 for 2003-2012 events | NOT SUPPORTED | gone in all sizes in the post-2003 sample | 400 to 600 (assumed) | 700 to 1,500 | **below (≈ 0)** | clears | not held (8-K text) |
| 7 | SEO / ATM, short; long after completion | short / long | Post-SEO underperformance ≈ 0 for 2003-2012; ATM: no drift figure found | NOT SUPPORTED (SEO); INSUFFICIENT (ATM) | ATMs are small issuers | hundreds | large | below | clears | cover shares held; 424B5 not held |
| 8 | Dividend initiations (long) / omissions (short) | long / short | +7.5 % / −11.0 % over 12 months (pre-1990s); not robust VW | NOT SUPPORTED (initiations) | equal-weighted only | 50 to 150 (assumed) | 25 to 75 a year | below on robust evidence | below | no declaration date |
| 9 | Index additions / deletions (not a SEC filing) | long | deletions −0.6 % in 2010-2020; +1.37 % in 2016-2020 (one paper) | NOT SUPPORTED (QI-19) | large caps | 20 to 30 each way | under 100 | below | below | not held |
| 10 | IPO lock-up expiry (FC-6) | short | −1.5 % over 3 days (pre-haircut) | MIXED | under $50M mostly | about 75 at ≥ $10M (store: 249 in 2020-2023) | under 255 | below | below | first bars held; terms parse-only |
| 11 | 8-K 4.02 non-reliance | short | No negative abnormal return in post windows, 2005-2016; positive later | NOT SUPPORTED | n/a | 100 to 200 (assumed) | n/a | below | n/a | `filing_events` 0 rows |
| 12 | 8-K 4.01 auditor change / resignation | short | Drift for at least 30 days after resignations; size UNVERIFIED | INSUFFICIENT | microcaps | resignations under 200 (assumed) | n/a | unknown | n/a | 0 rows |
| 13 | 8-K 5.02 CEO/CFO departure | either | Sudden deaths −1 % over 4 days, insignificant; no drift found | INSUFFICIENT | n/a | hundreds | n/a | below | clears | 0 rows |
| 14 | 8-K 1.01 / 2.01 | either | No drift found (1.01 merger agreements = row 2) | INSUFFICIENT | n/a | hundreds | n/a | unknown | clears | 0 rows |
| 15 | 8-K 3.01 delisting notice | short | Negative reaction; no drift figure | INSUFFICIENT | sub-$1 names | many, mostly under $10M | n/a | unknown | n/a | 0 rows |
| 16 | 8-K 1.03 bankruptcy | short | at least −12.5 % over 6 months; −29 % non-strategic | MIXED (pre-2005) | major-exchange names, then delisted | 30 to 80 listed (assumed) | under 100 | above (short) | below | 0 rows |
| 17 | NT 10-K / NT 10-Q | short | "continue to drift downward" for months; size UNVERIFIED | MIXED (one study; C1 retired) | small firms | about 235 (2,115 over 9 years in the study) | under 255 in band | unknown | below | NT forms not in the forms list |
| 18 | Going-concern opinion | short | −14 % over 1 year (1993-2005); "limited" after costs | MIXED (pre-2005, microcap) | microcaps | many, mostly under $10M | n/a | above (short) | n/a | not held (text) |
| 19 | Chapter 11 emergence (found in search) | long | +22.8 to +29.6 % over 200 days (1980-1993); median 0; gone in the 2010s | NOT SUPPORTED | OTC-traded | 20 to 40 | under 100 | below on recent evidence | below | not held |
| 20 | SPAC trust arbitrage (found in search) | long | ≈ 12 % a year (IPO to redemption, secondhand), mostly T-bill and warrants | INSUFFICIENT on excess return | SIC 6770 | hundreds in 2021, extreme clustering | small after clustering | excess below | below | SIC 6770 held; trust value not held |

**Shortlist (clears both lines plausibly): none.** The ranked nearest misses below are offered so the owner can see how far off each one is. None of them is a shortlist entry.

| Rank | Nearest miss | Gross (from next open) | n_eff 5y | Side | Band | Pass chance (closed form, E1 rule) | Single cheapest next check (counts only) |
|---|---|---|---|---|---|---|---|
| 1 | Officer/director purchase clusters, 60 sessions | 50 to 200 bp (prior centred near 100) | 420 to 2,400 (mid ≈ 1,000) | long | $10-200M (some ≥ $200M) | **≈ 18 to 20 %** (real-edge share ≈ 5 to 7 %) | Pin a cluster definition, then count cluster events by band at acceptance for 2019-2023 and 2017-2023, with distinct dates, distinct issuers and n_eff after same-date and 60-session same-issuer merging, from the held Form 4 table and close × volume levels. No price change |
| 2 | Cash merger arbitrage | ≈ 100 bp over T-bills | 110 to 270 | long | ≥ $10M | **≈ 30 %** at the event-study rule; **≈ 0** under line 40 against SPY | After the T324 index seed, count SC TO-T first filings and DEFM14A filings for targets at ≥ $10M, 2019-2023, matched to Form 25 |
| 3 | Activist 13D | unknown, likely under 200 bp | 70 to 250 | long | ≥ $10M | `deferred: power` at the shape; if read anyway ≈ 19 %, mostly noise (null ≈ 6 %) | Re-run C3's tradable-subset count at the ≥ $10M band with distinct filers (the frontier note's FIX) |
| 4 | Spin-off children | possibly 500 bp+ over 12 months | 25 to 100 | long | ≥ $10M | a pass means nothing (SE ≈ 450 bp) | Count 10-12B registrations whose child's first-bar band is ≥ $10M |

## Pass-chance arithmetic (closed form from PR #3683)

Rule (E1 shape): pass if the sample net mean ≥ max(30, SE). **P = Φ((gross − cost − max(30, SE)) / SE)**, with SE = sd / √n_eff. The weights on gross are this report's, not graded.

**Line check (the brief's figures reproduce).** At 200 bp gross and a 100 bp round trip, 80 % power needs 100 ≥ max(30, SE) + 0.84 SE, so SE ≤ 54.3. At sd 1,500 that gives n_eff 763, and at sd 866 it gives 254. At 300 bp: SE ≤ 108.7, so 190 at sd 1,500. At 150 bp the +30 floor binds: SE ≤ 23.8, so 3,970 at sd 1,500. **For small names the sd is wider:** at sd 2,000 the 60-session line is (2,000 / 54.3)² = **1,357**, not 765. This is an assumption, and it matters for candidate 1.

**1. Insider clusters**, sd 2,000 (assumed for $10-200M names, 60 sessions), 100 bp round trip:
- At n_eff 1,000 (SE 63): G = 50 gives Φ(−1.79) = 3.7 %. G = 100 gives Φ(−1.00) = 16 %. G = 150 gives Φ(−0.21) = 42 %. G = 200 gives Φ(0.59) = 72 %.
- Weights 0.45 / 0.30 / 0.15 / 0.10 give **20 %**. The G ≥ 200 term alone is 7.2 %.
- At n_eff 420 (SE 98): 6.5 / 16 / 31 / 51 % on the same weights gives **17.5 %**.

**2. Cash merger arbitrage.** Expected cost is 50 bp on entry plus 0.16 × 50 on breaks (84.2 % completion, a thesis figure, UNVERIFIED), so 58 bp. sd ≈ √(0.84 × 0.16) × (500 + 2,000) ≈ 900 bp, from an assumed +500 bp on completion and −2,000 bp on a break. At n_eff 200, SE = 64.
- P = Φ((G − 58 − 64) / 64). G = 50 gives 13 %, G = 100 gives 37 %, G = 150 gives 67 %.
- Weights 0.5 / 0.35 / 0.15 give **≈ 30 %**.
- 50 % needs G ≈ 122 bp; 80 % needs ≈ 176 bp.

**3. Activist 13D**, n_eff 130, sd 2,000 (SE 175): G = 0 gives 5.8 %, 100 gives 16 %, 200 gives 33 %, 300 gives 56 %. Weights 0.4 / 0.3 / 0.2 / 0.1 give 19 %. The null passes 5.8 %, so a pass carries little information. Stage 4a (c) at the shape's 80 % line fails.

**4. Spin-off children**, n_eff 60, sd 3,500 (12-month; C2's 0.35): SE = 452. A null (gross 0) passes Φ(−1.22) ≈ 11 % and a 500 bp edge Φ(−0.12) ≈ 45 %, so the read can barely tell them apart.

All four pass chances are for the event-study read only. The exam door (ADR 0016) still needs a paper-net IR of about 1.6 (PR #3683), which none of them is expected to have.

## Per-candidate evidence

Each block gives (1) the drift after the filing, (2) post-2015 evidence and grade, (3) where it lives by size, (4) events and n_eff, (5) side, (6) a matched peer, and (7) the store and the ingest it needs. All external figures are UNVERIFIED: they come from abstracts or search summaries, accessed 2026-11-20.

### 1. Officer/director purchase clusters (re-scoped from B7 / IC-B1)
1. **Drift.**
   - Alldredge and Blank (*J. Financial Research* 2019, 42(2), 331-360; Tier 1, abstract): "clustered insider purchases are followed by abnormal returns in excess of 2% during the subsequent month". A secondary summary gives about 2.1 % against about 1.2 % for solitary purchases.
   - Whether the clock starts at the trade or at the filing: UNVERIFIED. Sample years: UNVERIFIED.
   - "When are Insider Purchases Credible Signals of Private Information?" (2003-2022, more than 97,000 executive and director purchase filings; Tier 1 working paper via a Tier 2 blog summary): 60-day abnormal return **3.4 %** for non-acquisition purchases. The 5-day reaction from the Form 4 filing is about **2 %**.
2. **Post-2015.**
   - The 2003-2022 sample covers it partly, and it is post-publication (the insider literature dates from the 1980s), so no QI-2 haircut is applied.
   - **Disconfirmation:** G2-5 (UNGRADED; S3: "$30M-$500M: skipping day 1 leaves no significant alpha"); G2-6 (70 to 80 % of the alpha goes before disclosure); B7 desk read G2-A NOT SUPPORTED (liquid names, days).
   - Hossain finds the post-SOX two-day CAR from the filing rose to 1.13 % from 0.32 %, which is more front-loading, not less.
   - **Grade: MIXED.**
3. **Size.**
   - Lakonishok and Lee: strongest in small stocks. Jeng, Metrick and Zeckhauser: no size difference. Both via secondary summary, UNVERIFIED.
   - The store's officer/director purchase issuer-days for 2016-2023: ≥ $200M 2,224; $50-200M 5,160; < $50M 52,706 (census). The share of < $50M that is $10-50M is not counted. $10-50M names are 36 % of the < $50M pool in the cost note's universe (828 / 2,318). Purchases lean smaller, so **assume 20 to 35 %**.
4. **Count.**
   - Issuer-days at ≥ $10M: 2,224 + 5,160 + (10,500 to 18,400) = 17,900 to 25,800 over 8 years, so 2,240 to 3,220 a year.
   - Cluster share **assumed 15 to 30 %**, giving 335 to 970 events a year.
   - 5y: 1,680 to 4,830 raw; at B 2 to 4, **n_eff 420 to 2,400**.
   - 7y: 2,350 to 6,760 raw; **590 to 3,380**. 2017-2018 delister coverage is thin (B7 recount: fine from 2019).
   - Insider buying bunches after market drops (March 2020), so B near 4 is plausible.
5. **Side:** long.
6. **Matched peer:** yes (size, date, SIC from `classifications`).
7. **Store:** held. Acceptance-stamped Form 4 transactions, 2016-2026. Of officer/director purchase accessions, 46.7 % are accepted at or after 16:00 and 12.7 % before 09:30 (IC-B1 review). The mid-session entry the cost note requires (after 10:00) gives up part of the first day. No ingest is needed.

### 2. Cash merger arbitrage (going-private and merger targets after day one)
1. **Drift / spread.**
   - Verdad, 835 deals 2000-2020 (Tier 2 via Alpha Architect): about +2.0 % on completed deals, −2.8 % on cancelled ones, **1.5 % blended per deal**. Whether that is hedged or long-only, and cash or stock deals, is UNVERIFIED.
   - Jetley and Ji (*FAJ* 2010, Tier 1): average excess 5.31 % (daily method) and 3.52 % (monthly) a year, before 2010; 129 calendar days from bid to resolution.
   - Mitchell and Pulvino (*JF* 2001): about 4 %/yr after costs, 1963-1998 (prior IC-B3).
2. **Post-2015.**
   - Analysis Group (Tier 2): the spread has fallen by more than 400 bp since 2002.
   - A CBS thesis (Tier 3 by verification): 1998-2021 abnormal returns "decreased over time", not robust.
   - No clean post-2015 US cash-deal figure was found. **Grade: MIXED.**
3. **Size:** unknown for arbitrage returns. Easterwood et al. (JFQA) show announcement returns are about twice as large for small targets, but that is the day-0 jump, which we cannot capture.
4. **Count.**
   - Store Form 25 at ≥ $10M: 299 in 2020-08..2023-12, all reasons (cost note), so about 90 a year. At a 50 to 70 % cash share, 45 to 65 a year.
   - 5y: 225 to 325 raw, **n_eff 110 to 270** (B 1.2 to 2; deals sit on different dates, but breaks bunch in crises).
   - 7y adds almost nothing: Form 25 rows by effective year are 19 (2017) and 14 (2018).
5. **Side:** long.
6. **Matched peer:** not meaningful. The benchmark is the T-bill, which is why line 40 against SPY is the wrong yardstick (PR #3683, ambiguity 1; spec open question 40).
7. **Store:** Form 25 is held. The all-forms index (T318) is not seeded (T324 is an owner step). It needs SC TO-T (always cash), DEFM14A / PREM14A, 8-K 1.01, and offer terms (a text parse). Prior verdict: IC-B3 is `blocked`. The shape does not lift it: the 56 bp haircut gross is far under 200, and even the unhaircut ≈ 100 bp is under the own-shape 176 bp line.

### 3. Activist 13D (prior C3)
1. **Drift.**
   - Brav, Jiang, Partnoy and Thomas (*JF* 2008, Tier 1): about 7 to 8 % over [−20, +20]. The run-up starts 1 to 10 days before the filing. "No evidence of a negative abnormal drift during the 1-year period subsequent". So there is no reversal, and no measured continuation after the window either.
   - An unconfirmed working paper reports +11.4 % in the year after (C3, UNVERIFIED).
   - Bebchuk, Brav and Jiang (*Columbia LR* 2015): no long-term negative returns.
2. **Post-2015.**
   - Hartmann, Pelster and Sievers (2021, 2,689 engagements in 44 countries, Tier 1 WP): long-run BHARs have **declined** for recent engagements while announcement returns rose.
   - *IJBAF* 2023 (prior C3): post-filing returns significantly below pre-filing returns.
   - From 2024-02 the filing deadline is 5 business days, so less run-up happens before the filing; pre-2024 evidence may not transfer.
   - **Grade: MIXED** for the drift after the next open, and none of it is post-2015 US.
3. **Size:** the median 13D target trades about $2M a day. 1,430 of 9,523 initial 13Ds pass our price and volume rules (frontier note).
4. **Count.**
   - New activist 13Ds: 71 / 65 / 61 / 48 / 41 in 2017-2021 by one narrow count (Tier 2, Harvard forum). About 270 US campaigns a year by a broader count. 57 to 83 % are small or micro caps.
   - Assume **40 to 100 a year at ≥ $10M**. 5y: 200 to 500 raw, **n_eff 70 to 250** (B 2 to 3). 7y: 95 to 350.
   - The 60-session line is 765 (1,357 at sd 2,000). It fails even if the gross were 300 bp, unless n_eff is near 195 at sd 1,500.
5. **Side:** long.
6. **Matched peer:** yes.
7. **Store:** the adapter exists with 0 rows. It needs SC 13D, SC 13D/A and SCHEDULE 13D (XML from 2024-12), plus an activist filer list.

### 4. Spin-off parents and children (prior C2)
1. **Drift.**
   - McConnell, Sibley and Xu (*JPM* 2015, 2001-2013, Tier 1, abstract): buy-all-children beat the spin-off ETF, which beat the value-weighted market. Magnitudes UNVERIFIED. Parents' excess was driven largely by one outlier.
   - Cusatis, Miles and Woolridge (*JFE* 1993): parents and children both, 24 to 36 months.
2. **Post-2015:** only theses (CBS 2000-2022, "an unrelenting anomaly", Tier 3; Nordic, mixed). The prior note found CSD at about half the S&P after its 2016 index change (Tier 3). **Grade: MIXED.**
3. **Size:** small children.
4. **Count:** C2 found 8 to 12 readable events. 30 to 60 a year in total, 20 to 40 in band, gives 5y 100 to 200 raw. With 12-month overlapping holds, B 2 to 4 gives **n_eff 25 to 100**. At sd 3,500, a 500 bp edge needs n_eff 260 for 80 %.
5. **Side:** long (children).
6. **Matched peer:** yes.
7. **Store:** 10-12B is in the forms list; 76 spin-off corporate-action rows from 2020-11. Verdict unchanged: `deferred: power`.

### 5. Issuer tender offers and Dutch auctions (SC TO-I); 6. large buyback authorisations
- **Tender offers.**
  - Kadapakkam and Seth (*JF* 1994, Tier 1, abstract): buying after the announcement and tendering earned **1.74 %** over the tender period (1.36 % buying just before expiry), and stayed positive after spreads. 1980s sample.
  - No post-2015 study was found. **INSUFFICIENT.**
  - Count: operating-company offers are assumed at 15 to 40 a year; many SC TO-I filings are closed-end-fund tenders. n_eff 25 to 100. Fails both lines.
  - Store: not held (needs SC TO-I and SC TO-I/A in the index).
- **Buyback authorisations.**
  - Fu and Huang (*Management Science* 2016, Tier 1, abstract): 14,309 open-market repurchases, 1984-2012. The long-run abnormal returns "**disappear for the events in 2003-2012**".
  - Bargeron, Bonaime and Thomas (*JFQA*): the remaining drift comes from later authorisations and takeover bids.
  - **NOT SUPPORTED.** The count clears (about 700 to 1,500 n_eff, assumed) and the gross fails at about 0.
  - Long. Matched peer yes. Store: not held (8-K text, 10-Q repurchase tables). Already killed in the register as Q1 (net repurchasers, rung 2).

### 7. Equity offerings and ATM programmes
- **SEOs:** Fu and Huang show post-SEO underperformance disappears for 2003-2012 (6,739 SEOs). **NOT SUPPORTED** on the short side. There is no long-after-completion evidence.
- **ATMs:** Billett, Floros and Garfinkel (*JFQA* 2019) study the choice of ATM over SEO. No post-issue return figure was found. **INSUFFICIENT.**
- Short-side effects are ranked last. The issuance line was already killed three times (Q1, equity issuance desk read, XFIN).
- Store: cover shares are held; 424B5, S-3 and the 8-K 1.01 equity distribution agreement are not.

### 8. Dividend initiations and omissions
- Michaely, Thaler and Womack (*JF* 1995, Tier 1, abstract): +7.5 % for initiators and −11.0 % for omitters over 12 months, market-adjusted. The asymmetry shrinks when the change in yield is controlled for.
- Boehme and Sorescu (*JF* 2002, 1927-1998, Tier 1, abstract): significant equal-weighted, **insignificant value-weighted**, not robust across subsamples, "a sample-specific result of chance". Chen, Chou and Lee (2014) concur.
- **NOT SUPPORTED** for the long side; the omission drift is a short.
- Store: no declaration date (source B reject 17). It would need the 8-K 8.01 press-release text.

### 9. Index additions and deletions (not a SEC filing)
- Greenwood and Sammon (NBER w30748, Tier 1): deletions **−0.6 % in 2010-2020**, "not statistically distinguishable from zero". Vijh and Wang: +1.37 % for 50 downward deletions in 2016-2020, a single paper.
- QI-19 NOT SUPPORTED. FC-1 (Russell promotions, +2 % over 20 sessions, practitioner figure) is the register's nearest miss on gross outside filings, `deferred: power` on 3 dates. Unchanged.

### 10. IPO lock-up expiry (prior FC-6)
- −1.5 % over 3 days (Field and Hanka *JF* 2001), −0.6 % after the haircut, and short. Fails gross by a factor of three and is short-side.
- The store's count at day 180, ≥ $10M: 249 in 2020-2023. Unchanged.

### 11-15. 8-K items other than earnings
- **4.02 (non-reliance).**
  - Audit Analytics on 4.02 filers 2005-2016 (Tier 2): "no negative abnormal returns in any of the event windows". The exception is long-process restatements: −2.67 % in days 61 to 90. There are positive returns at days 271 to 300 (+1.55 %).
  - An EFMA 2009 paper finds positive returns over months 1 to 6.
  - **NOT SUPPORTED** for a short drift. Long side is unproven.
- **4.01 (auditor change).**
  - Choi, Mande and Son (*IJAF* 2020, Tier 1, abstract): a downward reaction from 60 days before a resignation to at least 30 days after. Magnitude UNVERIFIED.
  - Short, mostly microcaps. **INSUFFICIENT.**
- **5.02 (CEO/CFO departure).**
  - Jenter, Matveyev and Roth (449 CEO deaths 1980-2012, Tier 1 WP): about −1 % over four days, insignificant. No drift evidence found.
  - **INSUFFICIENT**; gross plausibly far under 200.
- **1.01 / 2.01.** No post-filing drift study was found. 1.01 merger agreements are row 2, and 2.01 completions end the arbitrage. **INSUFFICIENT.**
- **3.01 (delisting notice).**
  - Guragai (*Advances in Accounting* 2022, Tier 1, abstract): equity and multiple-deficiency notices draw more negative reactions than bid-price notices. No drift figure.
  - Sub-$1, short. Source B reject 26 stands.
- **1.03 (bankruptcy).**
  - At least −12.5 % over the six months after filing, for firms still on major exchanges, on top of −26 % on the day (Tier 1 via search summary).
  - Coelho and Taffler: −29 % for non-strategic filings (seminar slides, Tier 3 by verification).
  - Dawkins, Bhattacharya and Bamber (*JFQA* 2007): no post-filing reversals for 2000-2003 filings.
  - Short-only, delisting follows, borrow is scarce.
- **Store for all of 11-15:** `filing_events` code is merged, with 0 rows (T164e's real-store run not made).

### 16-18. Late filings (NT 10-K / 10-Q) and going-concern opinions
- **NT.**
  - Bartov and Konchitchki (Tier 1, 2,115 NT filings over nine years): −1.96 % (NT 10-K) and −2.93 % (NT 10-Q) on the reaction, and abnormal returns "continue to drift downward during the post-filing months".
  - Short. The mechanism is C1's, retired on survivor data. Not in the forms list.
- **Going concern.**
  - Kausar, Taffler and Tan (*JAR* 2009, 1993-2005, Tier 1, abstract): **−14 %** over a year. "After adjusting for transactions costs, the opportunity to earn profits … is limited."
  - Short, microcaps, pre-2005. **MIXED.** Not held (auditor-report text).

### 19-20. Found in search: Chapter 11 emergence; SPAC trust arbitrage
- **Chapter 11 emergence.**
  - Eberhart, Altman and Aggarwal (*JF* 1999; 131 firms, 1980-1993): +22.8 to +29.6 % over 200 days.
  - Duke FinReg blog (1981-2016): mean 40 to 50 %, **median zero**, driven entirely by OTC names.
  - Jiang, Wang and Yang (2021, via a CFA blog): gone in the 2010s.
  - **NOT SUPPORTED**; count about 20 to 40 a year.
- **SPAC trust arbitrage.**
  - About 12 % a year for buy-at-IPO-and-redeem (an SEC comment letter citing an unnamed study, Tier 3 by verification). Most of that is the trust's T-bill yield and the warrants.
  - Excess return over T-bills is INSUFFICIENT. Extreme date clustering (2021) collapses n_eff.

## Disconfirmation
- **Searches run** (33; titles abbreviated): Fu-Huang buyback and SEO disappearance; merger arbitrage 2010-2020 cash deals (×3); Alldredge-Blank clusters; insider purchases by size after the filing; insider purchases post-SOX decline; the 2003-2022 insider-acquisition paper; 13D post-filing drift 2010-2020; 13D day +1 small targets and the 2024 rule; activist campaign counts 2019-2023; Dutch auction post-expiry; Boehme-Sorescu; dividend initiation and omission replication; spin-offs post-2000; spin-offs 2015-2023 and CSD; Item 4.02 post-announcement drift; going-concern drift; bankruptcy post-filing drift; Chapter 11 emergence; buyback long-run 2010-2020; ATM offerings; CEO departure drift; S&P deletions 2010s; NT 10-K drift; auditor resignation drift; delisting-notice drift; SPAC trust arbitrage. Fetches failed for 3 hosts.
- **What was found against:**
  - Buybacks and SEOs are gone after 2003 (Fu and Huang).
  - Dividend drift is chance (Boehme and Sorescu).
  - Index deletions are about zero (Greenwood and Sammon).
  - Chapter 11 emergence is gone in the 2010s, with a median of zero.
  - 4.02 shows no post-window drift for 2005-2016.
  - The activist long-run return is declining (Hartmann et al.).
  - Merger spreads are down by more than 400 bp.
  - Insider purchases: front-loaded after SOX (Hossain), and the register's G2-5 S3 and G2-6.
  - The going-concern drift is "limited" after costs.
- **Who finds it gone, and since when:**
  - Buybacks and SEOs: since 2003 (Fu and Huang).
  - Index effects: since the 2010s (Greenwood and Sammon).
  - Chapter 11 emergence: in the 2010s (Jiang, Wang and Yang, secondary).
  - Merger spreads: since 2002 (Analysis Group).
  - Insider purchases in $30-500M firms after day 1: register G2-5.
- **Not found:**
  - A post-2015, net, by-size, next-open drift for any long-side filing event of 200 bp or more.
  - A post-2015 failed replication for insider clusters specifically. The question is open, not settled.

## Caveats & gaps
- **No full text was read.** Every external figure is UNVERIFIED. In particular, whether each "drift" starts at the filing, the next day or the trade date is unconfirmed, and that decides the from-next-open gross.
- **Event counts outside the store are assumptions.** These are the cluster share, the cash-deal share, the activist share in band, and the tender and dividend counts. The insider-cluster n_eff range (420 to 2,400) is wide because of them.
- **sd assumptions.** The 200 bp line uses sd 1,500 bp (60 sessions) from the E1 design, UNGRADED. For $10-200M names it is probably wider: at 2,000 the 60-session line is 1,357, not 765. Merger arbitrage's sd (about 900) comes from an assumed mixture.
- **The pass-chance weights are ours.** The closed form also counts passes on a true net edge of zero (16 % at any n_eff), which the exam door then has to kill.
- **The yardstick.** Merger arbitrage is judged against T-bills here. Line 40 as written uses SPY (PR #3683, ambiguity 1; open question 40). That is the owner's question, not this report's.
- **The 2017-2018 window adds little.** Delister coverage is thin before 2019 (census; B7 recount), so the 7y figures carry survivor risk.
- **Mid-session entry** (needed for the $10-200M cost of 12 to 17 bp a side) gives up part of the first day's drift on after-hours filings. No figure is available for how much.

## UNVERIFIED items
- Every literature figure in the map and the per-candidate sections, all read from abstracts or search summaries.
- The 2003-2022 insider paper's authors, its event-day convention, and its size split.
- Verdad's hedged/long-only and cash/stock split. The 84.2 % completion rate is from a thesis.
- Activist counts in band; the median activist target size after 2021.
- The 2024 13D deadline change (SEC rule, Federal Register not read).

## Follow-up questions (not answered here)
1. Does the owner accept a T-bill yardstick for a cash-like event book (open question 40)? Merger arbitrage's standing depends on it.
2. Should stage 4a (c) use each idea's own sd (wider for small names) when it applies the power line? The 765 line at sd 1,500 understates the need in $10-200M names.
3. Would a cluster card (re-scoped B7 / IC-B1) count as a new look in the B7 family under requirement 5, given that no store return was read for B7?

## Sources
All accessed 2026-11-20. Unless a line says "verified", what was read is the search engine's summary of the page, not the page itself.
- Fu and Huang, *Management Science* 2016, "The Persistence of Long-Run Abnormal Returns Following Stock Repurchases and Offerings". Tier 1. https://econpapers.repec.org/RePEc:inm:ormnsc:v:62:y:2016:i:4:p:964-984 · https://core.ac.uk/download/13247163.pdf
- Alldredge and Blank, *J. Financial Research* 2019, "Do Insiders Cluster Trades with Colleagues?". Tier 1, abstract. https://econpapers.repec.org/RePEc:bla:jfnres:v:42:y:2019:i:2:p:331-360
- "When are Insider Purchases Credible Signals of Private Information? Evidence from Acquisitions" (2003-2022). Tier 1 WP via Tier 2 blog. https://clsbluesky.law.columbia.edu/2026/09/22/when-are-insider-purchases-credible-signals-of-private-information/
- Hossain, post-SOX Form 4 informativeness (via CXO Advisory). Tier 2. https://cxoadvisory.com/investing-expertise/evolving-informativeness-of-insider-trading
- Verdad merger-spread study via Alpha Architect (Swedroe, 2023-04-14). Tier 2. https://alphaarchitect.com/merger-spread/
- Jetley and Ji, *FAJ* 2010; Analysis Group spread note. Tier 1 / Tier 2. https://analysisgroup.com/link/f5a479d9a8fb4799b20ff4a989d78a50.aspx
- NY Fed Staff Report 761 (merger arbitrage). Tier 1. https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr761.pdf
- Easterwood, Netter, Paye and Stegemoller (JFQA), "Taking over the size effect". Tier 1. https://vtechworks.lib.vt.edu/bitstream/10919/113235/2/ENPS_JFQA_PDF.pdf
- Brav, Jiang, Partnoy and Thomas, *JF* 2008. Tier 1. https://law.duke.edu/sites/default/files/centers/gfmc/session_3/2_brav_et_al-hedge_fund_activism-2008.pdf
- Hartmann, Pelster and Sievers 2021, TAF WP 059. Tier 1 WP. https://en.wiwi.uni-paderborn.de/fileadmin-wiwi/cetar/TAF_Working_Paper_Series/TAF_WP_059_HartmannPelsterSievers2021.pdf
- Harvard Law forum activism reviews (13D counts 2017-2021; small-cap shares). Tier 2. https://corpgov.law.harvard.edu/2022/10/25/shareholder-activism-in-2022/ · https://corpgov.law.harvard.edu/2024/01/04/activism-vulnerability-report/
- Kadapakkam and Seth, *JF* 1994 (Dutch auctions). Tier 1, abstract. https://econpapers.repec.org/RePEc:bla:jfinan:v:49:y:1994:i:1:p:291-306
- Bargeron, Bonaime and Thomas, *JFQA*, "Timing and source of long-run returns following repurchases". Tier 1. https://www.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/timing-and-source-of-longrun-returns-following-repurchases/3A0AA39EF30BB41483794352CDB40BAC
- Boehme and Sorescu, *JF* 2002. Tier 1. https://econpapers.repec.org/RePEc:bla:jfinan:v:57:y:2002:i:2:p:871-900
- Michaely, Thaler and Womack, NBER w4778 / *JF* 1995. Tier 1. https://www.nber.org/papers/w4778
- McConnell, Sibley and Xu, *JPM* 2015 (spin-offs 2001-2013). Tier 1. https://business.purdue.edu/faculty/mcconnell/publications/The%20Stock%20Price...2013%20jpm.2015.42.1.143.pdf
- CBS thesis "Spin-off performance: An unrelenting anomaly" (2000-2022). Tier 3 by verification. https://research-api.cbs.dk/ws/portalfiles/portal/98732383/1643122_Spin_off_performance_An_unrelenting_anomaly.pdf
- Audit Analytics, long-term consequences of restatements (2005-2016). Tier 2. https://blog.auditanalytics.com/long-term-capital-market-consequences-of-financial-restatements/
- Kausar, Taffler and Tan, *JAR* 2009. Tier 1. https://econpapers.repec.org/RePEc:bla:joares:v:47:y:2009:i:1:p:213-239
- Dawkins, Bhattacharya and Bamber, *JFQA* 2007; Taffler seminar slides. Tier 1 / Tier 3. https://papers.ssrn.com/ab_id=881508 · https://w4.stern.nyu.edu/finance/docs/pdfs/Seminars/083m-taffler.pdf
- Eberhart, Altman and Aggarwal, *JF* 1999; Duke FinReg blog; CFA Institute blog. Tier 1 / Tier 3. https://econpapers.repec.org/RePEc:bla:jfinan:v:54:y:1999:i:5:p:1855-1868 · https://sites.duke.edu/thefinregblog/2021/09/17/has-post-reorg-equity-outperformed-in-the-last-three-decades/
- Billett, Floros and Garfinkel, *JFQA* 2019 (ATMs). Tier 1. https://www.biz.uiowa.edu/faculty/jgarfinkel/pubs/ATM.pdf
- Jenter, Matveyev and Roth (CEO deaths). Tier 1 WP. https://www.ecgi.global/sites/default/files/2026-08/5728_good_and_bad_ceos.pdf
- Greenwood and Sammon, NBER w30748; Vijh and Wang. Tier 1. https://www.nber.org/system/files/working_papers/w30748/w30748.pdf · https://www.biz.uiowa.edu/faculty/avijh/SP_1.pdf
- Bartov and Konchitchki (NT filings). Tier 1. https://faculty.haas.berkeley.edu/yaniv/files/Papers_Publications/SEC-Filings-Regulatory-Deadlines-Capital-Market-Consequences_Bartov-Konchitchki_AH.pdf
- Choi, Mande and Son (auditor resignations). Tier 1, abstract. https://www.inderscience.com/filter.php?aid=106761
- Guragai 2022 (deficiency notices). Tier 1, abstract. https://ideas.repec.org/a/eee/advacc/v59y2022ics0882611022000359.html
- SEC comment letter S7-13-22 (SPAC redemption returns). Tier 3 by verification. https://sec.gov/comments/s7-13-22/s71322-20131049-300916.pdf
- **In the repo** (verified, read in full or in part): PR #3683, the pass arithmetic (scratch copy, "Answer", options row 5, event-study section); `2026-11-02-data-edge-census.md`; `idea-cards.md` (IC-B1 to B5, FC-1 to FC-6 and their reviews); `2026-11-02-cost-by-liquidity-band.md`; `ways-of-working/research-program.md` §4a; `hypothesis-backlog.md` (summary and ladder ledger: B7, B9, C1, C2, FC-*); `2026-10-27-sharper-candidates.md` (C2, C3); `2026-10-28-strategy-frontier-verified.md` (13D FIX); `claims.toml` (QI-2, G2-S10, QI-19, HO-4, HO-8, LS-6, SH-6, G2-5, G2-6).

## Proposed claims (not added to `claims.toml`)

| Proposed id | Statement | Kind | Grade | Scope |
|---|---|---|---|---|
| SF-1 | No SEC-filing event found in this search has published post-2015 evidence of a long-side drift from the next open of 200 bp or more per event in names of ≥ $10M daily dollar volume. | empirical (search result) | INSUFFICIENT | 20 event types; abstracts and summaries only; searches of 2026-11-20 |
| SF-2 | Long-run abnormal returns after open-market buyback authorisations and after SEOs disappear for 2003-2012 events (14,309 repurchases; 6,739 SEOs). | empirical | MIXED (one Tier 1 source; consistent with the register's Q1 and issuance kills) | US, Fu and Huang 2016, abstract |
| SF-3 | Ordinary officer/director purchases earn about 3.4 % abnormal over 60 days in 2003-2022, of which about 2 % arrives within 5 days of the Form 4 filing, and the effect is not significant after acquisitions (0.3 %). | empirical | UNGRADED (one WP via blog; event-day convention UNVERIFIED) | US, more than 97,000 filings, all sizes |
| SF-4 | On assumed cluster shares (15 to 30 %) and B = 2 to 4, officer/director purchase clusters at ≥ $10M daily dollar volume give an n_eff of about 420 to 2,400 over 2019-2023, against 765 needed (60 sessions, sd 1,500) or 1,357 (sd 2,000). | arithmetic on store counts plus assumptions | UNGRADED | census counts of 2026-11-02; cluster share assumed |
| SF-5 | Cash merger arbitrage per deal earns about 1.5 % raw (about 100 bp over T-bills) in 2000-2020. On its own cost shape (one side on completion) and a sd near 900 bp, it needs about 176 bp over T-bills for 80 % power at n_eff 200 under the E1 rule. | empirical plus arithmetic | UNGRADED (Tier 2 secondary; sd assumed) | US deals, Verdad via Alpha Architect |
| SF-6 | At sd 2,000 bp (an assumption for $10-200M names over 60 sessions), the 200 bp shape's 80 % power line is n_eff ≈ 1,357, not 765. | arithmetic | UNGRADED | PR #3683 closed form |
