# Research Report: Beyond long-only: long-short and hedged candidates (2026-10-10)

**Brief:** #1452 (owner direction 2026-10-10: "we need to stop with long only").  ·  **Date:** 2026-10-10  ·  **Status:** COMPLETE  ·  **Agent/model:** research workflow (scout, three skeptics per candidate, synthesis), claude-opus-5-5

**Question.** Six long-only momentum variants lost to SPY. If TradePartner drops the long-only constraint, which long-short or hedged books are worth testing in the ADR 0006 universe (~1,000 liquid US large caps, delisted names included, in-sample 2020-08-31 to 2023-12-29, prices from 2016)? Each book must also fit:
- the data we hold: SIP bars, corporate actions, EDGAR acceptance times, as-filed XBRL facts, point-in-time SIC, and 8-K items once T164 lands;
- a cost of ~15 bp per side plus borrow;
- Alpaca paper margin books under ADR 0017;
- a live account (#813, ~$100, cash) that cannot short.

What would the system need to run them, and what is the cheapest first step that tells us the most?

**Decision it feeds.** Whether to write a shorting ADR now. If so, how far it should reach (backtest only, paper, or live) and which test comes first. This report grades evidence and proposes backlog entries B11 to B15. It writes no hypothesis file and changes no ADR.

## Answer
**Verdict:** NOT SUPPORTED or INSUFFICIENT for every candidate as a net edge. SUPPORTED only for the narrow feasibility facts (we can short large caps cheaply on Alpaca paper).  ·  **Confidence:** medium-high for the negatives. Low for anything positive, because no test in our universe has enough statistical power.

In plain words:

1. **Lifting long-only changes what we measure, not how much edge we have.** The best evidence (Muravyev, Pearson and Pollet, JF 2025; Drechsler and Drechsler; Blitz, Baltussen and van Vliet) says most of a published anomaly's short-leg return sits in small, hard-to-borrow, high-fee stocks. Our liquid large caps are cheap to borrow, and that is exactly where the short leg earns least. Once trading and borrow costs are paid, the average anomaly long-short return is about zero.
2. **Five of the six strategy candidates were refuted by all three skeptics.** None has a source showing a positive net return after publication in liquid US large caps. They stay in this report with their low grades. The seventh entry (shorting practicalities) is a feasibility check, and no skeptic refuted it.
3. **There is a hidden retail cost.** As far as Alpaca's documents show, a retail account earns no interest (no "rebate") on short-sale proceeds. A market-neutral book then earns only its long-minus-short spread, while cash earns the T-bill rate. That drag was about 0 in 2021, about 2% in 2022, about 5% in 2023, and is still several percent a year. On its own it is larger than the expected net spread of every candidate here.
4. **A market-neutral book must not be judged against SPY.** A zero-beta book trails SPY by roughly the equity premium (about 7 to 10 pp/yr) by construction. Its fair yardstick is cash (T-bills), which needs an ADR 0005 amendment. An overlay that keeps beta near 1 (SPY plus a short sleeve) keeps SPY as its benchmark.
5. **The cheapest, most informative next step needs no ADR, no engine work and no order path:**
   - **B11:** split every long-only result we already have into market beta and stock-selection alpha. This tells us whether the momentum losses came from picking the wrong stocks or from market exposure.
   - **B12:** one registered long-short decomposition research run on the store. It measures the long leg, the short leg, and a stock-short book against the same long leg hedged with SPY.

   If B12 shows the stock short leg adds nothing over an SPY hedge (the expected result), the shorting build shrinks from size L to "short SPY in paper", or to nothing.

## Summary table

Grades are the post-skeptic grades of each candidate's core claim: a positive net return after publication, in liquid US large caps, net of trading and borrow costs. "Refuted" counts how many of the three skeptics refuted the core claim. "Right yardstick" names the benchmark each book should be judged against.

| # | Candidate | Core grade | Refuted | Right yardstick | Net prior on that yardstick (centre, range) | Capability | Cheapest useful step |
|---|---|---|---|---|---|---|---|
| 1 | Dollar-neutral composite (12-1 momentum + GP/A + low asset growth), with an SPY-hedged control | INSUFFICIENT (candidate said "MIXED leaning INSUFFICIENT") | 3/3 | T-bills | about −2 pp/yr over the in-sample (−4 at current rates), range −7 to +3 | L | B12 research run (S-M) |
| 2 | Three-signal composite (momentum, GP/A, cash-flow yield), dollar-neutral | INSUFFICIENT (candidate said "MIXED leaning weak") | 3/3 | T-bills | about −3 pp/yr, range −9 to +2 | L | merge into B12 |
| 3 | SPY 125 / 25 short overlay (losers on momentum + GP/A) | NOT SUPPORTED | 3/3 | SPY | about −0.5 pp/yr, range −2 to +0.5 (worse if Alpaca charges interest on the overlay debit) | L | short-leg readout inside B12 |
| 4 | Within-industry distance pairs (GGR with Do-Faff refinements) | NOT SUPPORTED | 3/3 | T-bills | about −3 pp/yr, range −7 to +2 | L (needs a new pair-state engine) | optional half-day gross spike, parked |
| 5 | Earnings-reaction (EAR) drift, long-short, ETB-only short leg | NOT SUPPORTED | 3/3 | T-bills | about −6 pp/yr, range −14 to +1 | L | short-leg diagnostic inside B9's first trial |
| 6 | Beta-hedged long book (B3 long, short β × SPY) | INSUFFICIENT as a strategy | 3/3 | T-bills | about −1.5 pp/yr, range −5 to +2 | L as a book; S as a diagnostic | B11 diagnostic (S) |
| 7 | Shorting practicalities (feasibility check) | SUPPORTED for feasibility on paper; NOT SUPPORTED for "a short leg creates a net edge here" | 0/3 | n/a | n/a | gate | start recording borrow status (B13) |

## Evidence common to every candidate

These findings carry the grades above. Each was checked by at least two skeptics unless marked otherwise.

- **Where the short-leg alpha lives.**
  - Muravyev, Pearson and Pollet (JF 2025; NBER draft May 2023) studied 162 anomalies with borrow-fee data from July 2006 to December 2020. The draft reports a long-short return of 0.15%/month gross, coming from the short leg, and −0.02%/month after borrow fees. The published abstract reports 0.14% and −0.01%.
  - The same paper finds the anomalies are not profitable *even before fees* once the 12% of stock-dates with fees above 1%/yr are excluded. An ETB-only large-cap short leg holds exactly the names left after that exclusion.
  - Drechsler and Drechsler (NBER w20282) find that eight major anomalies "effectively disappear within the 80% of stocks that have low short fees".
- **The short leg in large caps (the evidence conflicts).**
  - Blitz, Baltussen and van Vliet (FAJ 2020): most of a factor's value is in its long leg, and the short legs are subsumed by the long legs. The results are robust across size. The candidates' claim that the gap is *larger* in large caps is not in the source, and the Sharpe figures they quoted were not verified.
  - Israel and Moskowitz (JFE 2013) point the other way for momentum. Long positions supply almost all of size, about 60% of value and about half of momentum. They also find that "shorting becomes less important for momentum and more important for value as firm size decreases". So in large caps the momentum short leg matters *more*, gross. Three candidates misread this as "flat across size".
- **Post-publication size.** Chen and Velikov (JFQA 2023) put the average anomaly at about 4 bp/month net of effective spreads, post-publication and in the modern era. The FEDS working paper says 8 bp equal-weighted (4 value-weighted), 11 to 21 bp for the best quartile chosen in advance, and about 20 bp for combinations (abstract only). All of these figures are before price impact and borrow. The working paper adds that short fees of 10 to 20 bp "would wipe out" the average anomaly's remaining profit.
- **Retail funding.** Alpaca's margin and short-selling documents do not mention any interest or rebate on short proceeds. We assume zero. Measured against T-bills, a dollar-neutral book then gives up the full T-bill rate. This assumption is **unverified** and drives several priors. B12 states it, and a paper position can confirm it by reading the next-day accruals.
- **Statistical power.** The t-statistic is about Sharpe × √years. At a Sharpe of 0.5 that gives about 0.9 over the 3.3 in-sample years and about 1.35 over 2016 to 2023. The G4-9 hurdle is t > 3. A backtest in our universe can kill a candidate that is clearly negative, but it cannot confirm a positive one.
- **Ungraded register entries.** G4-3, G4-4, G4-5, G4-6, G4-7, G1-3, G2-S10, QI-2, QI-21, HO-2 and HO-13 are UNGRADED. Several candidates cited them as support, which research-program rule 3 forbids. Here they are used as context only. G4-1 is SUPPORTED, but only for long-short or unconstrained books, 1980-2014, excluding the bottom 20% by size, and not post-publication. It does not support shorting large caps after publication.

## Per-candidate sections

### 1. Dollar-neutral composite with an SPY-hedged control (`ls-factors`)
**Grade:** INSUFFICIENT for the core claim. All three skeptics refuted it, and "MIXED, leaning INSUFFICIENT" is not a grade in our scheme.

**The book.**
- **Signals:** three, fixed in advance (k = 3). 12-1 momentum, GP/A (TTM, as filed, at acceptance + 1 session) and low asset growth. Each is winsorized, z-scored, then averaged.
- **Legs:** long the top 20% and short the bottom 20%, with a 20/40 buy/hold buffer (Novy-Marx and Velikov). Equal weight, 1% name cap, 100/100, rebalanced monthly.
- **Control:** the same long leg against an equal-dollar short in SPY. The book minus the control measures what single-stock shorts add over an index hedge, which is the owner's question.

**Sources.**
- SUPPORTED: Jensen, Kelly and Pedersen 2023 (T1; 82% of 153 factors replicate, gross and before borrow).
- Novy-Marx and Velikov 2016 (T1; turnover under 50% keeps significant net spreads with a buffer).
- Novy-Marx and Velikov 2022 (T1; BAB is effectively equal-weighted, so BAB is rejected here).
- Israel and Moskowitz 2013, Blitz, Baltussen and van Vliet 2020, and Muravyev, Pearson and Pollet, as summarised above.
- Swedroe's "lost decade" summary (T3; the page returned 403 and was checked only through search snippets). Over 2010-2019 it reports profitability at about +1.7 pp/yr, momentum at about +3.5, and value, size and investment at about zero or negative.

**Disconfirmation.**
- Muravyev, Pearson and Pollet: the short leg is gone net of fees, and gone even gross in low-fee names.
- Post-2010 decay.
- The in-sample window contains both the 2020-11-09 momentum crash and the 2021 junk/meme rally.

**Prior (corrected).**
- Gross spread: about 1.5 to 3.5 pp/yr. Trading cost: about 1.5 pp/yr. Borrow: about 0.3 pp/yr. That leaves a net book return of about +0.5 pp/yr.
- Against T-bills, two skeptics found the candidate left out the no-rebate drag. With it, the centre is about −2 pp/yr at the in-sample average T-bill rate of about 1.8%, and about −4 pp/yr at 2023 rates. Range −7 to +3.
- Book minus SPY-hedged control: about 0, range −3 to +2.

**Costs, including borrow.**
- Turnover about 15 to 25% one-sided per leg per month, so trading costs about 1.0 to 2.2 pp/yr.
- Borrow: $0 on ETB names at Alpaca. The backtest charges a general-collateral (GC) rate of 25 to 50 bp/yr, with a stress rung of 300 bp or more on crowded names.
- Margin interest: 6.50% (5.00% elite) on any debit balance.
- 100/100 sits exactly at Reg T's 50% initial margin, so the book needs a gross cap below 2.0 or it has no headroom.

**Data and capability fit.**
- Signals: all held.
- SIC is held point-in-time (`store/classify.py::classifications_as_of`, CF-5), so financials (SIC 6000-6799) and utilities can be excluded directly. The "no gross_profit" heuristic is not needed.
- TTM figures need first-vintage YTD differencing (the B3 pitfall).
- 2016+ fundamentals need the facts backfill extended.
- Borrow history is not held.
- A dollar-neutral book is 2x gross, which breaks the charter's "no leverage" clause as well as "long-only".
- Capability size: L.

**Skeptic corrections.**
- Alpaca does offer HTB shorting with a locate on eligible margin accounts (docs, and a blog post of 2026-06-24). Alpaca's support page still says it is "not available", so the sources conflict.
- Paper has no locate endpoint and does not simulate borrow fees. A paper book can therefore *fill* HTB shorts at zero fee, the opposite of the candidate's claim. The deterministic risk rules must enforce an ETB-only gate.
- Whole-share shorts only: about 200 names at 1% each needs roughly $30k to $60k of short notional to keep weights on target.

**Information value per unit of cost.** Moderate, and it comes from the paired control, not from alpha. This becomes B12.

### 2. Three-signal composite with cash-flow yield (`multifactor-ls`)
**Grade:** INSUFFICIENT. All three skeptics refuted it. Its supporting evidence is pre-2010 (G4-1), includes small and mid caps (DeMiguel et al.), is long-only (G4-6, UNGRADED), excludes borrow (G4-4, UNGRADED), or comes from a many-signal institutional fund (AQR QMNIX, Tier 2, global, closed to new investors since 2026-06-19).

**The book.** The same skeleton as #1. The value signal is operating cash flow over market cap (CF/P), because book equity and earnings are not held. Long the top quintile, short the bottom, 20/30 buffer, 100/100.

**Own check (UNVERIFIED).** The candidate computed this from the Ken French big-cap 2x3 legs. The script is in scratch, not in the repo, and is ungraded. An equal mix of big-cap HML, RMW and UMD returned:

| Window | Gross return | t-stat |
|---|---|---|
| 1964-2009 | +3.9%/yr | 3.89 |
| 2010-2026 | +0.9%/yr | 0.66 |
| 2018-2020 | −2.4%/yr | |
| 2020-08 to 2023-12 | +0.3%/yr | |

That proxy uses B/M value-weighted legs, not CF/P equal-weighted quintiles. The candidate's "uplift" over it is asserted, not evidenced. Windows ending in 2026 include months after the development boundary (ADR 0016), so they must never be cited as out-of-sample evidence.

**Disconfirmation.**
- The big-cap mix since 2010 cannot be told apart from zero.
- DeMiguel et al.: the gain from many characteristics is significant only in the smallest 60% of stocks, and is not significant under a short-sale constraint.
- The 2018-2020 quant crisis (Blitz 2021, verified; the source frames it as a temporary storm).

**Prior.** Net book return about +0.5 pp/yr. Against T-bills with no rebate, about −3 pp/yr, range −9 to +2.

**Costs.** About 1.8 pp/yr of trading, plus 0 to 0.5 pp/yr of borrow.

**Skeptic corrections.**
- Alpaca cancels open short **orders** when a name turns HTB overnight. It does not force-cover existing positions (Alpaca's buy-in FAQ, found by search only). A held short can start paying HTB fees instead, so any forced-cover rule must be our own risk rule.
- A delisted short is not usually a gain. In a large-cap universe most delistings are acquisitions at a premium, which are losses for a short. The engine must use the cash-merger price.
- CF/P mixes an as-filed share count with a later close, so it needs split adjustment for splits between the share-count date and the rebalance.
- The variant count (sector-demeaned rank, beta-neutral, overlay, stress rungs) is 4 to 6 trials on a test with t below 1.
- G4-1's scope was inverted. It *excludes* the bottom 20% by size.

**Information value.** Same question as #1, and nearly the same book. **Merge it into B12** with one composite fixed in advance; CF/P can be a pre-declared single-signal leg, not a second composite.

### 3. SPY 125 / 25 short overlay (`short-overlay`)
**Grade:** NOT SUPPORTED. All three skeptics refuted it, and the candidate graded itself the same. Two of the three skeptics regrade the 130/30 transfer-coefficient argument from MIXED to INSUFFICIENT for our use: it multiplies an information coefficient we have not shown, and the 40-50% figure rests on a practitioner page.

**The book.** 125% SPY long. 25% short across the bottom decile (about 50 to 60 names) on momentum + GP/A, held until the name leaves the bottom 20%, with a 1% name cap. Net beta is about 1, so SPY stays the benchmark.

**Sources.**
- Drechsler and Drechsler (anomalies vanish among low-fee stocks).
- Chen and Welch, arXiv 2607.06502 (T2, UNGRADED as G2-S10): median long-short anomaly return of 7 bp/month after 2005 in the non-micro top 3,000, before costs.
- Huang and Wang (JFI 2013): no average alpha for 130/30 funds. The sample is crisis-only and mixes fund types, and their shorts *did* earn alpha; they just did not offset the long losses.
- ProShares CSM: a live large-cap 130/30 that trailed the S&P 500 by roughly 0.6 to 0.85 pp/yr. Snippet only, unverified.

**Disconfirmation.** The momentum-loser short leg is what crashes in junk rallies (2009, and 2020-11-09, which is inside our window).

**Prior (corrected).** About −0.5 pp/yr against SPY, range −2 to +0.5. Two corrections from the skeptics:
- Alpaca margin interest is 6.50% (5.00% elite), not 3.75%.
- It is unverified whether Alpaca nets short proceeds against the extra 25% of SPY. If it does not, the overlay carries a debit of about 25% of NAV, which costs about 1.25 to 1.6 pp/yr. That alone would sink it. A paper position can settle this.

**Costs.** Trading on short notional is about 1%/yr (about 0.25 pp/yr of NAV), plus SPY resizing. Borrow is $0 on ETB, or about 0.1 pp/yr of NAV at GC.

**Data and capability fit.**
- Signals are held.
- Borrow status is not point-in-time. Filtering on today's Alpaca flags would be look-ahead.
- Capability size: L.
- The candidate's PDT remark is wrong: PDT is triggered by same-day round trips, and FINRA has removed the rule anyway (see #7).

**Skeptic correction on the kill test.** The candidate proposed "an equal-weighted short basket trails SPY by more than 2%/yr". Mega caps dominated 2016-2023, so an equal-weighted basket can clear that bar from size alone, with no signal. The test must measure the basket against an **equal-weighted universe** (or a size-matched benchmark), and report the SPY comparison separately.

**Information value.** Low for the build. The short-leg readout is folded into B12. Parked as B14.

### 4. Within-industry distance pairs (`stat-arb`)
**Grade:** NOT SUPPORTED as a net edge after 2002 at 15 bp per side. All three skeptics refuted it, and the candidate graded itself the same. Historical gross returns are SUPPORTED: Gatev, Goetzmann and Rouwenhorst 2006 (up to 11%/yr), and Do and Faff 2010 (a steady decline; the subperiod numbers are unverified behind the paywall).

**The book.** The 12-month formation period ranks same-industry pairs by sum of squared deviations (SSD). Each cohort keeps the top 20 pairs that pass the zero-crossing filter. Trade a pair at 2σ, close at convergence or after 6 months, with 6 overlapping cohorts. Dollar-neutral.

**Sources.**
- Do and Faff 2012 (T1, verified): pairs and reversal are "largely unprofitable in the period post 2002". The 24 bp/month net alpha in the largest 30% of stocks is a full-sample figure (1963-2009), mostly pre-2002. The candidate omitted it.
- Rad, Low and Faff 2016 (T1): 91/85/43 bp gross and 38/33/5 bp net over 1962-2014, with fewer opportunities from 2009. The abstract also reports positive, significant alphas. The "unattractive levels" quote is CXO Advisory's wording, not the paper's.
- Guijarro-Ordonez, Pelger and Zanotti (Management Science 2025): on about 550 stocks, 2002-2016, the parametric OU benchmark reaches a gross Sharpe of only 0.73 to 0.97. Only a deep model survives, at net Sharpe 0.94 to 1.24, at 5 bp cost plus 1 bp *per day* on shorts (about 2.5%/yr).
- In the register: SH-1 NOT SUPPORTED, SH-2 SUPPORTED, SH-4 MIXED, QI-17 MIXED.

**Prior (corrected).** Gross about +2 pp/yr, less about 2 to 4 pp/yr of trading at 2x gross. Against T-bills with no rebate, the centre is about −3 pp/yr, not the candidate's "interest is a wash" figure of 0.

**Data fit.** Better than the candidate thought. SIC is held point-in-time (CF-5, `classify.py`), so the industry restriction has no look-ahead. Alpaca's `shortable`/`easy_to_borrow` flags are already mapped in `alpaca_broker.py`, but they are current-only.

**Capability.** L, including a new pair-state engine, so it is not an amendment to an existing family.

**Information value.** Low. Parked as B15. If a shorting seam exists later, the half-day gross spike (kill if gross per round trip is under 60 bp per $1 one-sided, or under 3%/yr on committed capital) is the only cheap next step.

### 5. EAR drift, long-short (`event-ls`)
**Grade:** NOT SUPPORTED (proposed LS-12). All three skeptics refuted it, and the candidate graded itself the same. SH-6 stays MIXED for the gross long-short.

**The book.** B9's long leg, plus a short in the bottom quintile by the [−1,+2] market-adjusted return around the 8-K Item 2.02 acceptance. Held 20 sessions. ETB-only short leg. Daily membership changes.

**Key evidence.** Muravyev, Pearson and Pollet Appendix Table 1 (verified by two skeptics from the PDF), EAR decile 1:
- abnormal return −0.43%/month with all stocks;
- −0.03%/month without stocks whose fees exceed 1%/yr;
- −0.20%/month net of fees.

The "93% from high-fee names" figure is our own arithmetic, not a number the paper reports. The conflict: the CZ value-weighted EAR short leg made about 0.49%/month gross over 2010-2024 (SH-6). Muravyev, Pearson and Pollet's portfolios are equal-weighted, use DGTW benchmarks, and drop stocks under $1 or $50M. That conflict goes in the claim's note.

**Prior.** About −6 pp/yr against T-bills at 100/100, range −14 to +1. Each leg turns over about 100 to 200% a month, which costs about 3.6 to 7.6 pp/yr per leg.

**Skeptic corrections.**
- HTB shorting is an Alpaca option with a locate and a fee, so skipping HTB names is our choice, not a broker limit.
- Margin interest is 5.00/6.50%.
- The SSR (Rule 201) trigger can be computed exactly from the daily low against the prior close. It binds only after a −10% day on day +2 or +3.
- Chen and Welch's 48 bp (all stocks, pre-2005) and 7 bp (top 3,000, post-2005) mix a change of period with a change of universe.

**Information value.** The full book is poor value. **Do not open a separate hypothesis.** Instead, make the bottom-quintile short-leg return (gross, against SPY, against an equal-weighted universe and against cash) a pre-declared output of B9's first trial; the extra cost is close to zero.

Lead for later: composite equity issuance (Daniel-Titman) is one of the few short legs that survives Muravyev, Pearson and Pollet's low-fee screen (−0.25%/month low-fee, −0.17 net of fees). It is buildable from the held shares fact and belongs to the issuance/payout angle.

### 6. Beta-hedged long book (`hedged`)
**Grade:** INSUFFICIENT for "a beta-hedged B3 earns a positive net return over T-bills". All three skeptics refuted it as a strategy, and all three endorsed its diagnostic.

**The arithmetic.** With no rebate, the hedged return is about alpha + (1 − β)·rf. A hedge removes the market; it adds no edge. So "hedging improves the return against SPY" is false by arithmetic (proposed LS-13, a derived fact).

**Long-minus-market against long-short: MIXED (LS-14).**
- For long-minus-market: Blitz, Baltussen and van Vliet.
- Against: Benaych-Georges, Bouchaud and Ciliberti (CFM 2021, T2, verified from the full text). At $1B AUM, 2000-2020, globally, net of real costs, a five-factor long-short book has a Sharpe of 0.98 against 0.56 for hedged long-only.
- But: their single-factor hedged books are "comparable, on average" to long-short. Their hedged leg also carries a futures hedge that embeds the T-bill rate, which a retail SPY short does not get.
- Their SMB point: hedging an equal-weight-ish long leg with a cap-weighted index mixes in a size bet. That is the same trap B12 must avoid.

**Skeptic corrections.**
- ADR 0005 does not make SPY a target. Its benchmarks (SPY *and* MTUM) are "measured against, never targeted", and beating one is "explicitly optional". The binding precedent is the B2 parking decision (#659: a higher Sharpe at a lower return was rejected; revisit only with an ADR 0005 amendment).
- McLean-Pontiff's decay is 26% out of sample and 58% after publication (QI-2). The candidate's 32%/65% figures are wrong.
- Reg T fit holds only for β̂ ≤ 1, so the hedge ratio needs a cap.
- Paper simulates neither dividends nor borrow fees ("Coming Soon"). For a short-SPY book that is a gap of about 1 to 2 pp/yr between paper and backtest.
- The synthetic hedged series must use a trailing, point-in-time beta, never a full-sample one.
- A trigger like "re-raise if any book shows alpha t > 2", scanned across 8 or more books, needs deflating.

**Information value.** Low as a book. High as the size-S diagnostic: this becomes B11.

### 7. Shorting practicalities (`practicalities`)
**Grades:**
- Feasibility of a large-cap, ETB-only, monthly short leg on Alpaca **paper**: SUPPORTED. No skeptic refuted it.
- "A short leg turns our known anomalies into a net edge in this universe": NOT SUPPORTED. Two of the three skeptics firmed this up from "INSUFFICIENT to NOT SUPPORTED". One carve-out: the momentum short leg in large caps stays INSUFFICIENT, given the Israel-Moskowitz size result.

**Verified facts (Alpaca docs and the repo, checked 2026-10-10 by two or three skeptics).**
- The paper account is already a margin account with `shorting_enabled` true and a 4x multiplier ([2026-10-08-alpaca-paper-facts](2026-10-08-alpaca-paper-facts.md)).
- Shorting and margin need at least $2,000 of equity; below that, buying power is 1x.
- Reg T is 50% initial, 2x overnight.
- Short maintenance is the greater of $5/share or 30% for stocks at $5 and above, and the greater of $2.50/share or 100% below $5.
- Margin interest is 6.50% (5.00% elite), computed on a 360-day basis on debit balances.
- ETB names carry a $0 locate and borrow fee for Trading API users (since 2025-10-01).
- HTB names need a locate in 100-share round lots, not reusable, plus a daily fee of value × rate / 360. Alpaca's pages conflict on whether HTB is available at all.
- No fractional shorts (HO-13).
- Open short orders are cancelled before the open when a name turns HTB overnight.
- Paper does not simulate dividends (on either leg) and lists borrow fees as "Coming Soon". The locate endpoint is reportedly absent in paper (third-party sources).
- FINRA's Rule 4210 amendment removes the PDT designation and the $25k minimum. The SEC approved it in April 2026 (sources say the 14th or the 15th). It took effect 2026-06-04, with a phase-in to 2027-10-20. It is irrelevant at our cadence either way.

**Data.**
- No historical borrow fees or ETB status are held.
- Today's Alpaca flags are current-only, so a backtest that applies them to history has look-ahead.
- FINRA's free short-interest files cover exchange-listed stocks **only from June 2021** (RN 21-19). A short-interest proxy for HTB status would therefore miss about 10 months of the in-sample window, and the backtest would need the flat-fee sensitivity for those months.
- D'Avolio (2002): market-wide value-weighted borrow cost of about 25 bp/yr. That figure comes from one lender's book.
- Daniel, Klos and Rottke: the share of stocks with fees above 1% rose to 43% by 2022. That count is equal-weighted across mostly small stocks.

**Skeptic corrections.**
- The "+0.5 pp/yr vs cash" prior holds only against a zero benchmark. Against T-bills it is about −3 to −4 pp/yr.
- Do not add 15 bp on top of G4-4's 4 bp: that figure is already net of spreads, so it would double-count.
- Turnover cost at 20 to 35% a month is about 0.7 to 1.3%/yr per leg, not up to 2.5%.
- Dividends owed on shorts are a cash flow, not a net cost; on a dollar-neutral book the long leg's dividends roughly offset them.

**Information value.** High as a gate. It also supplies most of the "research report on borrow cost and availability" that ADR 0015's shorting row lists as a precondition. The missing part is a point-in-time record, which is B13.

## What the system needs

Mapped to the capability steps. Sizes are S, M or L. "Who needs it" lists the candidate numbers from the summary table.

| Step | What | Size | Who needs it | Notes |
|---|---|---|---|---|
| 0 | B11 diagnostic: CAPM alpha and beta of existing long-only trials, and a synthetic hedged-against-T-bill line | S | 6, and every candidate as a gate | Reads `trial_equity`, SPY and a T-bill proxy (SGOV or BIL bars from the same SIP source, or FRED DTB3 with `known_at` at publication). No engine or order change. `metrics.risk_free_rate` already exists. |
| 1 | B12 long-short decomposition research run | S-M | 1, 2, 3, and 5's leg | Computes leg returns from bars and facts as a registered research run. It is logged and counts in N. It needs the T-bill proxy from step 0. It needs the fundamentals backfill extended to reach 2016 (M), or it runs momentum-only from 2016 and the composite from 2020-08. |
| 2 | Shorting ADR (class B, owner) | S to write | all | It must: amend charter line 16's "long-only" **and** "no leverage"; supersede ADR 0010's "no shorts, ever" and the pinned `refused_position_side` refusal; lift ADR 0014's ban on signed strategy output; open ADR 0015's "Shorting and margin" row; keep #813 long-only; scope shorting to paper books. Seam 5's frozen keys (`portfolio.side`, `portfolio.gross_exposure`) enter at their defaults, so H1's fingerprint does not move. |
| 3 | ADR 0005 amendment: a cash/T-bill benchmark and objective for market-neutral books; SPY and MTUM stay for overlays | S | 1, 2, 4, 5, 6 | Reverses the B2 objective decision, so it is the owner's call. |
| 4 | Signed positions in the backtester | M-L | all books | `_Book`, valuation, fills and `_close_fifo` for short lots; the `engine.py` negative-value raise (lines 443-448); short dividends (`reconcile.py`); delisting returns for shorts at the cash-merger price. |
| 5 | Borrow, financing and margin model | M | all books | `CostsConfig`: a GC rate, a stress rung, HTB round-lot fees, no rebate, margin interest. `RiskConfig`: net and gross caps (gross below 2.0), a per-name short cap, a $5 price floor, Alpaca's maintenance table, and our own cover rule when a name loses ETB status. |
| 6 | B13 borrow-status record | S (snapshot) + S-M (FINRA ingest) | any future paper short book | A daily snapshot of Alpaca `shortable`/`easy_to_borrow`, and optionally IBKR's public file. A FINRA short-interest ingest with `known_at` at the publication date. New data ingest means an ADR under the research-program rule. |
| 7 | Paper order path | L | paper books | `position_side` in five tables (DuckDB cannot alter a CHECK in place, so this is a table rebuild over real rows); sell-short and buy-to-cover intents; the cover phase; the id side token; `next_attempt`. An ETB-only pre-trade gate, because paper will not refuse HTB. Safety-reviewer and quant-auditor. |
| 8 | Live shorting | owner decision | — | Needs a margin account of at least $2,000, and realistically $30k to $60k for a 150- to 200-name whole-share short book. Not on #813. |

**Recommendation.**
1. Run **B11 now** (S). It is the cheapest test and needs no ADR.
2. Then run **B12** as one registered research run, with its rules fixed in advance:
   - long leg, short leg and composite, gross and at 15 bp per side;
   - borrow at 0 / 30 / 300 bp;
   - against T-bills, with the short leg also measured against an **equal-weighted** universe;
   - the stock-short book compared with the same long leg hedged with SPY.

   Park the stock-short build if the gross composite spread over 2016-2023 is not clearly above +2 pp/yr, or if the book does not beat its SPY-hedged control.
3. The owner can write the shorting ADR at any time. Scoping it to **backtest and paper** keeps the order-path work (step 7, size L) behind the B12 result.
4. If B12 is negative, the most that is worth building is a short-SPY hedge in paper (steps 2 to 5, at about M), or nothing.

One open question for the owner: does a long-short *measurement* run (B12) need the shorting ADR first, or is it research measurement under ADR 0013? We read it as measurement, but that is his call.

## Proposed backlog entries

| ID | Item | Tests | Data | Engine | Cost | Status |
|---|---|---|---|---|---|---|
| B11 | Beta/alpha decomposition of every long-only trial (H1, six momentum variants, B3 once run), with a synthetic hedged-against-T-bill line using a trailing, point-in-time β | LS-13, LS-14, LS-15 | have (adds a T-bill proxy series) | none: research query on `trial_equity` | S | proposed, first |
| B12 | Long-short decomposition research run: momentum + GP/A + asset-growth composite (CF/P as a pre-declared single leg), long and short legs separately, book against SPY-hedged control, against T-bills and against an equal-weighted universe, borrow at 0 / 30 / 300 bp | LS-2, LS-3, LS-6, LS-7 | have, plus the T-bill proxy; facts backfill to 2016 optional (M) | research run; no signed engine needed | S-M | proposed, after B11; gate for steps 2 to 7 |
| B13 | Borrow-status record: daily Alpaca ETB/shortable snapshot; FINRA short-interest ingest (`known_at` at publication) | LS-18, LS-19 | new (ingest ADR) | none | S + S-M | proposed; only if the owner opens shorting |
| B14 | SPY 125 / 25 short overlay, and its 130/30-over-B3 variant | LS-7, LS-8 | as B12 | signed engine, margin, borrow | L | parked: NOT SUPPORTED; reconsider only if B12's short leg trails an equal-weighted universe by more than 2%/yr gross |
| B15 | Within-industry distance pairs | LS-9, LS-10 | have (point-in-time SIC) | new pair-state family, plus shorting | L | parked: NOT SUPPORTED |
| (B9 amendment) | EAR short-leg diagnostic as a pre-declared output of B9's first trial | LS-12 | as B9 | none extra | ~0 | proposed for B9's file |

## Proposed claims for claims.toml (prefix LS-, one per finding)

The "Proposed as" column shows the id the scout used. All ids move to the LS- prefix because the register uses one prefix per source report.

| ID | Statement (short) | Kind | Grade | Scope / note | Proposed as |
|---|---|---|---|---|---|
| LS-1 | Classic factors (momentum, profitability, investment, value), long-short, earned positive premia that replicate | empirical | SUPPORTED | gross, before borrow, mostly pre-2010, capped value weights (JKP 2023). The Hou-Xue-Zhang part was not re-verified | LS-1 |
| LS-2 | A momentum + profitability (+ investment or CF/P) long-short composite in the top ~1,000 US stocks nets a positive return after 15 bp per side and borrow, post-2010 | empirical | INSUFFICIENT | no direct test; own French-leg check +0.9%/yr gross, t 0.66 (UNVERIFIED, not in repo) | LS-2, MF-1 |
| LS-3 | In large caps, single-stock shorts add net value over an index hedge of the long leg | empirical | NOT SUPPORTED | net of trading and borrow, post-2006 (Muravyev-Pearson-Pollet; Blitz-Baltussen-van Vliet). Conflicts gross with Israel-Moskowitz on momentum (short leg matters more in large firms). 1 of 3 skeptics graded MIXED | LS-3 |
| LS-4 | BAB/low-beta long-short earns a premium in value-weighted large caps | empirical | NOT SUPPORTED | Novy-Marx-Velikov 2022 | LS-4 |
| LS-5 | Value long-short in the largest stocks | empirical | MIXED | Israel-Moskowitz; the lost decade (T3, unverified). Not buildable faithfully: no book equity | LS-5 |
| LS-6 | Anomaly long-short returns come from the short leg, are about zero net of borrow fees, and are unprofitable even gross excluding high-fee stock-dates | empirical | SUPPORTED | equal-weighted CRSP ex <$1/<$50M, 2006-2020, DGTW (JF 2025: 0.14 / −0.01; draft 0.15 / −0.02); concurs with Drechsler-Drechsler. Conflict: SH-6's CZ VW short leg | — |
| LS-7 | A short overlay of anomaly losers adds net return over SPY in liquid US large caps after 2005 | empirical | NOT SUPPORTED | net of trading and borrow | (overlay) |
| LS-8 | Lifting the long-only constraint raises the IR of our signals (transfer coefficient) | empirical | INSUFFICIENT | conditional on an IC we have not shown; the 40-50% figure comes from a practitioner page | (overlay) |
| LS-9 | Distance pairs earned large gross excess returns historically, declining steadily | empirical | SUPPORTED | gross, pre-2009 (GGR 2006; Do-Faff 2010, subperiod figures unverified) | SA-1, SA-2 |
| LS-10 | Distance pairs net a positive return after 2002 in liquid US large caps at 15 bp per side | empirical | NOT SUPPORTED | Do-Faff 2012; Rad-Low-Faff 2016; the full-sample 24 bp large-cap alpha is pre-2002-dominated | SA-3, SA-6 |
| LS-11 | Residual (PCA/ETF) stat arb survives costs in large caps | empirical | MIXED | only deep models at 5 bp to 2016; parametric OU gross Sharpe below 1 | SA-4 |
| LS-12 | EAR long-short with an ETB-only short leg is profitable net of trading and borrow, post-2006 | empirical | NOT SUPPORTED | conflicts with SH-6 (CZ VW short leg ~0.49%/month gross 2010-2024) | EL-1 |
| LS-13 | A beta-hedged long book trails SPY by about the equity premium unless its alpha exceeds it | fact (arithmetic) | SUPPORTED | R_H ≈ α + (1−β)·rf with no rebate | HG-1 |
| LS-14 | A long leg hedged with an index captures most of a factor premium (long-minus-market ≥ long-short) | empirical | MIXED | Blitz-Baltussen-van Vliet against Benaych-Georges-Bouchaud-Ciliberti; contaminated by an SMB artefact when the hedge is cap-weighted | HG-2 |
| LS-15 | A beta-hedged B3 long book earns a positive net return over T-bills post-publication | empirical | INSUFFICIENT | B3 has not run | HG-3 |
| LS-16 | Alpaca terms: ETB $0 locate and borrow; $2,000 minimum for margin and shorts; maintenance max($5, 30%) at ≥$5 and max($2.50, 100%) below $5; margin interest 6.50% (5.00% elite); no fractional shorts; HTB fees in round lots | fact | SUPPORTED | Alpaca docs checked 2026-10-10; HTB *availability* conflicts across Alpaca pages | — |
| LS-17 | Retail short proceeds earn no rebate at Alpaca | fact | INSUFFICIENT | docs silent; confirm on paper; drives the T-bill drag in every market-neutral prior | MF-2 |
| LS-18 | Alpaca paper: margin account with shorting enabled; no dividend simulation; borrow fees "Coming Soon"; locate reportedly absent | fact | SUPPORTED (locate: third-party only) | paper flatters shorts; the backtest and reconciliation must charge dividends and borrow | — |
| LS-19 | FINRA free short-interest files cover exchange-listed stocks only from June 2021 | fact | SUPPORTED | a point-in-time HTB proxy misses 2020-08 to 2021-05 | — |
| LS-20 | Composite equity issuance's short leg survives a low-fee screen | empirical | INSUFFICIENT | one appendix row (−0.25%/month low-fee, −0.17 net, 2006-2020); a lead for the issuance angle | — |

## Disconfirmation
- **Searches run (across the seven angles):**
  - the role of shorting (Israel-Moskowitz; Stambaugh-Yu-Yuan);
  - short-sale costs (Muravyev-Pearson-Pollet; Drechsler-Drechsler; D'Avolio; Daniel-Klos-Rottke);
  - long legs against long-short (Blitz-Baltussen-van Vliet; Benaych-Georges-Bouchaud-Ciliberti);
  - post-publication net returns (Chen-Velikov; Chen-Welch; McLean-Pontiff);
  - BAB construction (Novy-Marx-Velikov 2022);
  - 130/30 live records (Huang-Wang; ProShares CSM);
  - quant crises (Blitz 2021; AQR QSPIX);
  - pairs decay (Do-Faff; Rad-Low-Faff; Pelger et al.);
  - Alpaca margin, borrow, HTB and paper docs;
  - FINRA short interest; the FINRA 4210 PDT change.
- **What was found against:** every candidate. The strongest single item is Muravyev, Pearson and Pollet's result that the anomalies are unprofitable before fees once high-fee stocks are excluded.
- **What was found for:**
  - Israel-Moskowitz on the momentum short leg in large firms (gross).
  - Benaych-Georges, Bouchaud and Ciliberti: long-short beats hedged long-only net, but at institutional funding.
  - AQR QMNIX's live record: 7.54%/yr against 2.04% for T-bills since 2014. It is many-signal, global and institutionally funded, so it is not replicable here.

## Caveats & gaps
- No candidate was tested on our data. Every prior is literature plus arithmetic.
- The no-rebate assumption (LS-17) is unverified, and it moves every market-neutral prior by the T-bill rate.
- Backtest borrow is a flat GC charge on an assumed all-ETB universe. That is most wrong for the bottom quintile, which is the leg it prices.
- Comparing an equal-weighted book with cap-weighted SPY over 2016-2023 builds in a size effect (mega-cap dominance). B12 must report against an equal-weighted universe as well.
- Any figure from windows after 2023-12-29 (French data to 2026, QMNIX calendar years) saw post-boundary data and is never out-of-sample evidence (ADR 0016).
- Taxes: every short gain is short-term. This does not matter on paper.

## UNVERIFIED items
- The French big-cap mix computation (`ffcalc.py`, in scratch, not in the repo).
- ProShares CSM since-inception figures (13.86% against 14.42% or 14.71%; snippet only).
- AQR QSPIX 2018-2020 calendar returns, and QMNIX calendar returns from Yahoo.
- Blitz-Baltussen-van Vliet's Sharpe figures (1.10 / 0.69 / 0.86).
- Novy-Marx-Velikov 2022's $1.05 figure.
- Do-Faff 2010's subperiod numbers, and Do-Faff 2012's 0.45%/month for 1989-2009.
- Rad-Low-Faff's "peaked around 1990 / unattractive levels" wording (CXO's summary).
- Drechsler-Drechsler's IVOL numbers.
- The Swedroe lost-decade numbers (403).
- Zhu 2024 (student work, T3).
- That Alpaca paper lacks the locate endpoint, and that Alpaca does not force-cover existing shorts on an HTB flip (third-party and search-only sources).
- No short rebate at Alpaca (docs silent).
- The SEC approval date of the PDT change (14 or 15 April 2026).
- The current T-bill rate for October 2026.

## Follow-up questions (not answered here)
- Does Alpaca net short proceeds against long purchases, or charge interest on an "adjusted debit" that includes shorts? One paper position answers this, and it decides B14.
- Is #813 a true cash account, or Alpaca's default limited-purpose margin account? Below $2,000 it cannot short either way.
- Does B12, a long-short measurement run, need the shorting ADR first, or is it research measurement under ADR 0013? This is the owner's call.
- Should the shorting ADR reach paper order-path work at all before B12 reports?

## Sources
1. Muravyev, D., Pearson, N. D., & Pollet, J. M. Anomalies and Their Short-Sale Costs. *Journal of Finance* (2025); NBER conference draft 2023-05-17. https://conference.nber.org/conf_papers/f184100/f184100.pdf
2. Drechsler, I., & Drechsler, Q. The Shorting Premium and Asset Pricing Anomalies. NBER w20282. https://www.nber.org/papers/w20282
3. Israel, R., & Moskowitz, T. (2013). The role of shorting, firm size, and time on market anomalies. *JFE* 108(2). https://ideas.repec.org/a/eee/jfinec/v108y2013i2p275-301.html
4. Blitz, D., Baltussen, G., & van Vliet, P. (2020). When Equity Factors Drop Their Shorts. *FAJ* 76(4). https://ideas.repec.org/a/taf/ufajxx/v76y2020i4p73-99.html
5. Benaych-Georges, F., Bouchaud, J.-P., & Ciliberti, S. (2021). Equity Factors: To Short Or Not To Short. arXiv 2003.10419. https://arxiv.org/pdf/2003.10419
6. Chen, A. Y., & Velikov, M. (2023). Zeroing In on the Expected Returns of Anomalies. *JFQA*; FEDS WP 2020-039. https://www.federalreserve.gov/econres/feds/zeroing-in-on-the-expected-returns-of-anomalies.htm
7. Chen, A. Y., & Welch, I. (2026). What Useful Alphas? arXiv 2607.06502. https://arxiv.org/abs/2607.06502
8. Novy-Marx, R., & Velikov, M. (2016). A Taxonomy of Anomalies and Their Trading Costs. *RFS* 29(1). https://www.nber.org/papers/w20721
9. Novy-Marx, R., & Velikov, M. (2022). Betting against betting against beta. *JFE* 143(1). https://ideas.repec.org/a/eee/jfinec/v143y2022i1p80-106.html
10. Jensen, T., Kelly, B., & Pedersen, L. (2023). Is There a Replication Crisis in Finance? *JF*. https://nber.org/system/files/working_papers/w28432/w28432.pdf
11. McLean, R. D., & Pontiff, J. (2016). Does Academic Research Destroy Stock Return Predictability? *JF* (register QI-2).
12. Stambaugh, R., Yu, J., & Yuan, Y. (2012). The Short of It. *JFE*. https://www.nber.org/papers/w16898
13. DeMiguel, V., Martin-Utrera, A., Nogales, F., & Uppal, R. (2020). A Transaction-Cost Perspective on the Multitude of Firm Characteristics. *RFS* (register G4-1, G4-3).
14. Blitz, D. (2021). The Quant Equity Crisis of 2018-2020. *JPM*. https://www.robeco.com/en-us/insights/2021/02/the-quant-equity-crisis-of-2018-2020-cornered-by-big-growth
15. Huang, J., & Wang, Y. (2013). Should investors invest in hedge fund-like mutual funds? Evidence from 130/30 funds. *JFI*. https://pure.psu.edu/en/publications/should-investors-invest-in-hedge-fund-like-mutual-funds-evidence-/
16. Gatev, E., Goetzmann, W., & Rouwenhorst, K. G. (2006). Pairs Trading. *RFS* (cited by the scout through a blog; use the RFS paper).
17. Do, B., & Faff, R. (2010). Does simple pairs trading still work? *FAJ* 66(4); (2012). Are pairs trading profits robust to trading costs? *J Fin Research*. https://strathprints.strath.ac.uk/41722/
18. Rad, H., Low, R. K. Y., & Faff, R. (2016). The profitability of pairs trading strategies. *Quantitative Finance*. https://research.bond.edu.au/en/publications/the-profitability-of-pairs-trading-strategies-distance-cointegrat/
19. Guijarro-Ordonez, J., Pelger, M., & Zanotti, G. Deep Learning Statistical Arbitrage. *Management Science* (2025); arXiv 2106.04028.
20. Avellaneda, M., & Lee, J.-H. (2010). Statistical arbitrage in the US equities market. *Quantitative Finance* 10(7).
21. D'Avolio, G. (2002). The market for borrowing stock. *JFE*.
22. Daniel, K., Klos, A., & Rottke, S. (2025). Inefficiencies in the Securities Lending Market (via Swedroe). https://larryswedroe.substack.com/p/the-rising-cost-of-short-selling
23. AQR Equity Market Neutral Fund (QMNIX) fund page, as of 2026-09-30. https://funds.aqr.com/funds/aqr-equity-market-neutral-fund
24. Alpaca: Margin and Short Selling docs (https://docs.alpaca.markets/docs/margin-and-short-selling); short-selling fees support page; zero ETB borrow-fee blog; HTB locates blog; paper-trading docs.
25. King & Spalding on the FINRA Rule 4210 amendment (2026). https://www.kslaw.com/insights/articles/finra-adopts-sweeping-changes-to-margin-requirements-for-day-trading
26. FINRA Equity Short Interest data. https://www.finra.org/finra-data/browse-catalog/equity-short-interest/data
27. Repo context:
    - [charter](../charter.md) Scope line;
    - ADRs [0005](../decisions/0005-objective-benchmark-stop-criteria.md), [0010](../decisions/0010-phase-4-risk-rules.md), [0014](../decisions/0014-strategies-as-registered-objects.md), [0015](../decisions/0015-expansion-seams.md), [0016](../decisions/0016-development-boundary-and-forward-exams.md) and [0017](../decisions/0017-fast-paper-and-machine-readiness-gate.md);
    - [short-horizon candidates](2026-10-09-short-horizon-candidates.md) (SH-1 to SH-12);
    - [Alpaca paper facts](2026-10-08-alpaca-paper-facts.md);
    - [hypothesis backlog](hypothesis-backlog.md) (B1 to B10);
    - [claims.toml](claims.toml) (G4-1, CF-5, QI-2, HO-13);
    - `src/tradepartner/backtest/engine.py` (the negative-value raise), `src/tradepartner/store/classify.py` (`classifications_as_of`) and `src/tradepartner/adapters/alpaca_broker.py` (the `shortable`/`easy_to_borrow` mapping).
