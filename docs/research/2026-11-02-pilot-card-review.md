# Research Report: independent review of the idea-pipeline pilot's 11 cards

**Brief:** #2914 (the [idea-pipeline spec](../specs/idea-pipeline.md), #2896, requirement 7 step 2, work item 5)  ·  **Date:** 2026-11-02  ·  **Status:** COMPLETE (counts, dates, stamps and liquidity bands only; no return read)  ·  **Agent/model:** team pilotreview, Claude Opus 5.5

## Answer

**Verdict:** n/a (a review, not a test of a claim). The findings are on each card in [idea-cards.md](idea-cards.md). This note gives the evidence behind them.  ·  **Confidence:** high on the store counts; medium on the literature (abstracts and one full text read, see the Evidence table); low on every dispersion (sd) the cards assume, which nobody can check without a return read.

The review **changed three verdicts and confirmed eight**. After review, 2 of the 11 cards are admitted (both `blocked`), 4 are `deferred: power` and 5 are `rejected`. No card is `admit`.

| Card | Before | After | Why it changed, or what was confirmed |
|---|---|---|---|
| IC-B1 | rejected: q3 | **confirmed** | The floor argument holds. G2-5's S3 sample ($30M to $500M, skipping day 1 leaves no significant alpha) is direct evidence against the band's tradable gross. Corrected: 47% of officer/director purchase accessions are accepted after 16:00 in the store, not "over 70%" |
| IC-B2 | blocked: data field (later vintages) | **confirmed**; q3 gross unconfirmed | No primary source shows drift after a quiet revision. Files-Swanson-Tse (2009) show a smaller reaction at disclosure, not slower incorporation. At 2.0x the margin sits on D9's line with an invented 50 bp |
| IC-B3 | blocked: data field (deal filings, terms) | **deferred: power** | In the development window (2020-08-31 to 2023-12-29) the store holds at most 34 + 109 = **143** common delistings at ≥ $50M (every reason, every deal type) against n_eff 160. The cash-deal subset is smaller. The card's "marginal yes, 173" counted 2019-2023 |
| IC-B4 | blocked: data field (8-K items) | **rejected: question 1** | Segal & Segal (RAS 2016) test this exact mechanism on non-earnings 8-Ks (2005-2013): managers do time bad news for after hours and Fridays, but the paper finds **no** investor inattention in the reaction. Michaely-Rubin-Vedrashko (JFE 2016) find the Friday effect is selection |
| IC-B5 | rejected: q3 | **confirmed** (on q7 more than q3) | The ≤ 10 bp gross has no primary source, but power fails by two orders of magnitude: 92 issuer-days on 26 issuers at ≥ $200M in 2020-2023 against n ≈ 10,000 |
| FC-1 | deferred: power | **confirmed**; cost line corrected | Names ranked 900 to 1,100 on the rebuilt rank day are 0 to 3% in the ≥ $200M band, 19 to 28% in $50-200M and 72 to 81% under $50M. The "11x at arrival" applies to almost no event. On re-entry the card is `blocked: cost measurement (< $50M, $50-200M)`. The Dimensional figure is 20 names a year, value-weighted |
| FC-2 | deferred: power | **confirmed** | No primary source exists for the pre-announcement window. Practitioners publish the predicted list (ORTEX), so the window is contested |
| FC-3 | rejected: q3 | **confirmed** | Corrected: the paper's sample is 1997-2023, so the development window has been **seen**. A bond series is **not held** (only SPY among SPY, AGG, BND, TLT, IEF, IEI, SHY, GOVT and LQD). A practitioner rebuttal (L&G 2025) finds no effect from 2020-01 to 2025-06 |
| FC-4 | rejected: q3 | **confirmed** | Etula et al. verified from the full text: −31% cumulative over T−8 to T−4 in 2003-2013 (US sample 1980 or 1995 to 2013) |
| FC-5 | deferred: power | **confirmed** | No post-2005 US prior-year-loser January figure was found. The latest primary source found is Haug-Hirschey (FAJ 2006, data to 2004). The ≤ +1% has no source |
| FC-6 | blocked: side short (paper) | **blocked: cost measurement (< $50M)** first, then side short (paper), borrow, lock-up terms | Of 1,080 non-SPAC common first bars from 2020-02 to 2023-06 (an IPO proxy), the band at day 180 is ≥ $200M **26**, $50-200M **56**, < $50M **998**. In the measured band power fails (26 against 180). The events are in < $50M, where the fill-cost note's context half-spread alone is +116 bp a side against a 60 bp gross. Corrected: the filing index is **absent** from the store copy; the rung 2-O volume check can run on first-bar dates + 180 days |

**Ranked on the evidence (a view, not a selection, which is the owner's):**
1. **FC-1** (Russell R2000→R1000 promotions). It has the best mechanism evidence in the batch. Chang-Hong-Liskovich (RFS 2015) is peer-reviewed and uses a regression-discontinuity design: deletions from the R2000 see lower returns from the index flow. The +2% after implementation comes from an ungraded practitioner source and covers 20 names a year. The trade is long-only and dated by rule. It can be killed without a return read (the volume spike). The rank-day rebuild from held fields lands within about 6% of FTSE Russell's published breakpoints. Against it: three June dates in the window, a cost that is unmeasured for almost every name, and a forward exam with two dates a year from December 2026, which makes confirmation slow (years, not months).
2. **IC-B3** (cash merger arbitrage). The only true risk premium, Tier 1 (Mitchell-Pulvino JF 2001: 4%/yr after costs, 1963-1998, payoff like a short put), and long-only. But it is now `deferred: power` in the window and its announcement data is not held.
3. **FC-6** (lock-up expiry). Tier 1 (Field-Hanka JF 2001, abstract verified: −1.5% over three days, +40% volume, 1,948 lock-ups), with dates spread out, so clustering is low. But the reason it persists is costly borrow on young IPOs, which is the cost we cannot measure. Its events sit in < $50M names, and it needs a short.
4. **IC-B2** (stealth revisions). Original, but with no primary source for the drift, and it needs a short.

FC-2, FC-5 and the five rejected cards are weaker on the evidence.

**The single gap that touches the most cards of distinct mechanisms is cost measurement below $200M of daily dollar volume** (the < $50M and $50-200M bands). Four mechanisms wait on it: FC-1, FC-5 and IC-B3 on re-entry, and FC-6 now. Rough size: S to M, about 1 to 2 agent-days plus two weeks of a small paper book trading those bands to meet ADR 0023's fill count. **It admits no card on its own.** FC-6 still needs a short paper book. FC-1, FC-5 and IC-B3 also need events, that is history: the 2016-2019 recovery that team probedelist is testing, or pre-2016 data (ADR 0022). Closing both cost measurement and history would move FC-1 to `admit` if history adds enough June dates. Four more June dates would give at most about 70 effective events at ρ = 0.1, against 36 needed at +2.0% and 144 at +1.0%. So FC-1 clears power only at the unhaircut practitioner figure.

## What ran

| Item | Value |
|---|---|
| Store | `scratchpad/replay/store-20261102.duckdb` (schema 41), `read_only=True`. No `data/` file and no `.env` opened |
| Scripts | `pilotreview/r1_form4.py` (sha256 prefix `bf60b150fe4e8f25`), `r2_misc.py` (`0721679d9a512581`), `r3_bands.py` (`0a24b8a200b84473`) in the session scratchpad, not committed (every number they produced is in this note) |
| Read | Row counts; `accepted_at`, `known_at`, `filed_at` and `first bar` dates; roles and codes; 20-session median `close × volume` (liquidity band); `close × cover shares` on three rank days (a size rank, for the band of names near #1,000). **No price change, return or performance was read** |
| Cutoffs | Event counts end 2023-12-29. Field counts use `known_at` before 2026-10-01. **Two disclosed exceptions:** the first-bar date was a `min(session)` over every bar (its values used are all before 2023-07, but the scan touched rows after 2026-10-01), and the SPY row of the ETF check printed the series' last date and count (2026-10-30, 2,730 bars). Neither is a price or return value. Both are recorded here as reads of dates past the census cut, the spec's requirement 8 rule for a breach. The first table listing counted all rows of every table. The `statement_restated` figure is the 2026-10-06 ingest run's counter, as in the census |
| Web | Abstracts and pages for every paper in the Evidence table. Full text read only for Etula et al. (the open copy) |

## Store checks, card by card

| Card | Card's claim | Store (this review) | Agrees? |
|---|---|---|---|
| IC-B1 | `known_at = accepted_at` on 100% of Form 4 rows | 100% of 3,704,146 rows (3,687,139 known before 2026-10-01) | yes |
| IC-B1 | o/d code-P issuer-days at $50-200M: 5,160 (2016-2023), 972 issuers; 2,839 in 2020-2023 | 5,161, 973 issuers; 2,840 (706 issuers) in 2020-2023; by year 495 to 846 | yes (±1, band edge) |
| IC-B1 | "over 70% accepted after the close" (G2-2) | Of 71,236 o/d purchase accessions accepted 2018-11 to 2023-11: 46.7% at or after 16:00 New York, 12.7% before 09:30; 59.4% outside the session | **no**: G2-2 is another sample's figure; the entry rule (first open after acceptance) is unaffected |
| IC-B5 | 10%-owner-only issuer-days: 148 at ≥ $200M (35 issuers), 496 at $50-200M (92) | 148 (35), 496 (92); 2020-2023: 92 (26) and 248 (60) | yes |
| IC-B2 | 253,797 differing later values against 2,134,064 first-vintage keys | The 2026-10-06 run's message: `statement_added: 2134064 … statement_restated: 253797` | yes |
| IC-B2 | one accession per (CIK, fact, period) | 0 keys with more than one accession; 304,059 rows carry `comparative = true` (first vintages taken from a comparative column, not revisions) | yes |
| IC-B3 | Form 25 common delistings 2019-2023: ≥ $200M 45, $50-200M 128 | 46 and 129 (2019-2023); **34 and 109 in 2020-08-31..2023-12-29**; all 780 are exchange-filed `25-NSE` forms | yes, but the window the strict tier would use is smaller |
| IC-B3 | `mergers` holds 35 completions known before 2026-10-01 | 24 cash, 8 stock, 3 mixed = 35; earliest `known_at` 2026-07-28 for effective dates from 2025-08 (backfilled, so `known_at` is ingest time, not completion time) | yes |
| IC-B3, IC-B4 | `filing_events` 0 rows; all-forms filing index not seeded | `filing_events` 0 rows; no `filing_index` table in the copy; `sec_filing_events` holds only forms 4 (2,054,863) and 4/A (40,752) | yes |
| Census | common names by band on 2020-08-31 / 2021-12-31 / 2023-12-29 at ≥ $200M: 238 / 362 / 319 | 235 / 355 / 313 (latest classification per security) | yes (method) |
| FC-1 | membership rebuildable from close × `shares_outstanding` | 3,873 / 4,326 / 4,269 common names with a cover-share count on the 2021-04-30 / 2022-04-29 / 2023-04-28 closes. The rank-1,000 cap is $4.96B / $4.36B / $4.08B against FTSE Russell's published breakpoints of $5.2B / $4.6B / $4.2B. **Rank day was in May for 2021 (data as of 2021-05-07)**, not the last business day of April that the card's current rule gives; each year's rank day must be pinned | yes, about 5% low; rank-day dates corrected |
| FC-1 | promoted names "mid caps", cost +8.8 bp at ≥ $200M | Names ranked 900 to 1,100 by 20-session dollar volume: 2021 ≥ $200M 5, $50-200M 52, < $50M 144; 2022 1 / 56 / 144; 2023 0 / 38 / 163 | **no**: the measured band holds almost none |
| FC-3 | bond series UNVERIFIED | Of SPY, AGG, BND, TLT, IEF, IEI, SHY, GOVT and LQD, only SPY is in `listings` | not held |
| FC-6 | `filing_index` holds 424B4s since T318 | Table absent from the copy | **no** |
| FC-6 | about 500 expiries 2020-08..2023-12 | 1,427 common first bars from 2020-02 to 2023-06, of which 347 carry a SIC 6770 row (ex-SPAC). Non-SPAC: 1,080 (2020 263, 2021 541, 2022 207, 2023 69), on 574 distinct first-bar dates. The proxy also catches uplistings and other new listings, so it is an upper bound on IPOs | count plausible; band composition was not on the card |
| FC-6 | liquidity of expiring names | Day-180 band of the 1,080: ≥ $200M 26, $50-200M 56, < $50M 998 | **no**: the events sit below the measured band |
| FC-6 | borrow unmeasured | `borrow_status` exists with 0 rows | yes |

## Cost arithmetic, recomputed

Every card's arithmetic was redone. It reproduces to rounding: break-even 2 × 8.8 = 17.6 bp (48.4 bp against the open); D9 = 35.2 bp; QI-2 × 0.42; n = (sd / effect)²; the effective count per date is m / (1 + (m − 1)ρ). Two notes:

- **IC-B3:** Mitchell-Pulvino's 4%/yr is already **after their transaction costs**. Charging our cost again is conservative, not an error.
- **IC-B2:** the 30 bp/yr borrow is conservative for easy-to-borrow names (the register's Alpaca terms give ETB $0). The 2.0x margin rests entirely on the unsourced 50 bp gross.

What changed was not the arithmetic but **which band each card's events fall in**: FC-1 and FC-6 apply the ≥ $200M figure to events that are almost all under $200M of daily dollar volume.

## Evidence

| Claim | Source | Tier | Key figure | Post-pub? | Net? |
|---|---|---|---|---|---|
| Russell cut-off price pressure (FC-1) | Chang, Hong & Liskovich, RFS 28(1) 2015, [NBER w19290](https://www.nber.org/papers/w19290) | 1, abstract | price effects for additions and for deletions at the R1000/R2000 cut; reversal not in the abstract | No | No |
| R2000→R1000 reversal (FC-1) | [Dimensional, Roberts & Dai 2025](https://www.dimensional.com/us-en/insights/even-migrating-stocks-face-index-reconstitution-costs) | practitioner, ungraded | 2014-2023, VW against the R2000, **20 names per group per year**: −1.1% in the 20 days before, about +2% in the 20 after, volume about 12x | Yes | No |
| Russell breakpoints (FC-1 rebuild check) | FTSE Russell recon releases via [search](https://www.lseg.com/content/dam/ftse-russell/en_us/documents/other/2023-russell-recon-recap-final.pdf) | primary (index provider) | $5.2B (2021, data as of 2021-05-07), $4.6B (2022), $4.2B (2023, 2023-04-28) | n/a | n/a |
| Nasdaq-100 window (FC-2) | Biktimirov & Xu 2019; Brock thesis (via search); [ORTEX](https://public.ortex.com/nasdaq-100-december-annual-reconstitution-final-ranking) | thesis / practitioner | transitory, symmetric response around the effective date; predicted lists published | — | No |
| Month-end rebalancing (FC-3) | Harvey, Mazzoleni & Melone, [NBER w33554](https://www.nber.org/papers/w33554) 2025; L&G rebuttal 2025 | 2 (WP); practitioner | 17 bp next day, **sample 1997-2023**; rebuttal: no relation 2020-01..2025-06 | No | No |
| Dash for cash (FC-4) | Etula, Rinne, Suominen & Vaittinen, RFS 33(1) 2020, [open copy](https://acris.aalto.fi/ws/portalfiles/portal/40965155/hhz054.pdf), **full text read** | 1 | 2003-2013: −31% cumulative over T−8..T−4, +103% over T−3..T+3; US samples 1980-2013 and 1995-07..2013-12 | No | No |
| January / tax-loss (FC-5) | Haug & Hirschey, FAJ 62(5) 2006 | 1, abstract | small-cap January premium persists to 2004; tax-loss explanation weakened | Partly | No |
| Lock-up expiry (FC-6) | Field & Hanka, JF 56(2) 2001 | 1, abstract | 1,948 lock-ups, −1.5% three-day abnormal, +40% volume, larger with VC backing; "no complete explanation" (the 1988-1997 sample years not seen in the abstract) | No | No |
| Stealth restatements (IC-B2) | Files, Swanson & Tse, TAR 84(5) 2009 | 1, summary | three-day returns −8.3% / −4.0% / −1.5% by prominence high / medium / low; lawsuits 27% / 16% / 0% | No | No |
| Immaterial corrections (IC-B2) | Choudhary, Merkley & Schipper, CAR 2021 | 1, abstract | modest negative reaction; predict later errors, weaknesses and comment letters (not "restatements") | No | No |
| Merger arbitrage (IC-B3) | Mitchell & Pulvino, JF 56(6) 2001 | 1, abstract | 4,750 mergers 1963-1998; 4%/yr excess **after transaction costs**; correlation with the market rises in downturns | No | Yes (theirs) |
| Spread shrinkage (IC-B3) | Jetley & Ji, FAJ 2010 (via search) | 1, secondary | spread still positive, significantly lower since 2002 | Yes | No |
| Friday announcements (IC-B4) | Michaely, Rubin & Vedrashko, JFE 122(1) 2016 | 1, abstract | after a selection correction, no evidence of less attention on Fridays | — | — |
| Non-earnings 8-K timing (IC-B4) | Segal & Segal, RAS 21(4) 2016 | 1, abstract | 2005-2013: managers report negative non-earnings news after hours and on Fridays; **no evidence of investor inattention** in the reaction | — | — |
| Unaffiliated holders least informed (IC-B5) | Seyhun, JFE 16 1986 | 1, not retrieved | UNVERIFIED (the abstract was not found; the ranking is as commonly cited) | No | No |

## Disconfirmation

- **For FC-1, the strongest card:** QI-19 (index effects shrank; Greenwood-Sammon) and the figure's own scope (20 names a year, value-weighted, a practitioner piece with no standard errors). CHL's design isolates the flow at the cut, but its abstract gives no post-implementation reversal number. If the reversal is half the practitioner figure, the card needs 144 effective events, and even pre-2016 history would be tight.
- **For IC-B3:** no clean post-2010 US cash-deal study was found. Practitioner and thesis figures range from 4% to 8% a year and are not graded.
- **For IC-B4's rejection:** Segal & Segal's market-reaction result is from the abstract; a full-text read could show a subset (Friday evening only) with a delayed response. Michaely-Rubin-Vedrashko's companion JAE 2016 paper finds that only Friday-evening earnings announcements look opportunistic. The card can re-enter if a primary source shows a delayed response for Friday-evening non-earnings items after the selection correction.

## Question 5 and capabilities

- **Question 5.** Every card's ledger row exists (L-B1 to L-B5, FC-1 to FC-6), and every relative the cards name checks out against the ledger and `claims.toml`. Additions: FC-5 should name the **momentum family's registered trials**, whose store reads covered 2020-08..2023-12 and so the Januaries of a loser-heavy construction. IC-B2 and FC-6 should name **LS-6** (SUPPORTED: anomaly short legs are about zero net of borrow).
- **Capabilities.** The vocabulary was used correctly on all cards. Fixes:
  - FC-3's bond series is `data field: not held`, not UNVERIFIED.
  - FC-6's "held suffice: yes" stands, but through first-bar dates, not the filing index.
  - The vocabulary has no borrow line; FC-6's borrow is recorded under `cost measurement` (`borrow_status` table, 0 rows).

## Caveats

- The FC-1 rebuild uses cover-page shares and one class per security. It does not apply Russell's float, country or price rules, nor the band (which needs last year's membership). The band split of ranks 900 to 1,100 is indicative, not a promoted list.
- The FC-6 first-bar proxy includes non-IPO new listings. Its band split is the part this review relies on, and it would be the same or lower for true IPOs, many of which trade thinly by day 180 (UNVERIFIED).
- Every dispersion (sd) on every card is still an assumption. The review did not and could not check them without a return read.

## Sources

Store copy `store-20261102.duckdb` (schema 41), read-only; [idea-cards.md](idea-cards.md); [ladder ledger](hypothesis-backlog.md#ladder-ledger); [claims.toml](claims.toml) (G2-2, G2-5, LS-6, QI-2, QI-19, TP-9); [fill-cost note](2026-10-30-paper-fill-cost-arrival.md) (band definition: 20-session median dollar volume; context half-spreads +116 / +46 / +20 bp); web sources linked in the Evidence table.
