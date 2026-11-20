# Research Report: Price, volume, earnings-number and supply-date events against the 200 bp shape

**Brief:** #3688  ·  **Date:** 2026-11-20  ·  **Status:** INCOMPLETE on verification, complete as a screen. Every candidate in the brief was searched and placed on the map. But no full text could be opened: the two full-text hosts tried were outside the session's allowed network, so every literature figure below comes from a search-result summary or an earlier repo report, and none is checked against a page. See "What is missing"  ·  **Agent/model:** team shapeprice. 27 web searches, 2 failed fetches. No store was opened, no return or count was read, nothing was registered. The brief text was taken from the orchestrator's restatement of #3688, because the issue itself could not be read from this session

## Answer
**Verdict:** NOT SUPPORTED. No event built from prices, volumes, earnings numbers or known supply dates has published evidence after 2015 of a long-side drift of 200 bp or more, measured from the first tradable price, in names trading $10M a day or more, with enough events to reach the n_eff line. Two candidates cannot be ruled out: the counts clear, but the size of the effect is unknown after 2015. One idea has the gross and fails on the count, and it is the nearest miss  ·  **Confidence:** medium on the negatives (several independent sources agree on decay and on where the effects sit by size); low on the two open candidates (no figure after 2015, no figure for our liquidity bands); low on every count (the counts below are arithmetic on stated assumptions, not counts from the store)

1. **Nothing found clears both lines on evidence.** For each idea the brief lists, the published drift after the event either (a) predates decimalization in 2001 and has shrunk since, (b) lives in illiquid or microcap names, where costs take most of it, (c) sits on the short side, or (d) comes from an event that happens on one date a year, so it can never reach a few hundred independent events. Most ideas fail on two of the four.
2. **Nearest miss: FC-1 (Russell 2000 stocks promoted to the Russell 1000).** It is the only idea with a published post-2014 effect at the 200 bp line: about +2% over the 20 sessions after reconstitution (Dimensional, 2014-2023; Tier 2; UNVERIFIED here). It fails the count by about ten times. There is one date a year, with about 20 names a date and shared moves, so 2017-2023 gives at most about 50 effective events against the 255 to 490 needed. Its card's verdict, `deferred: power`, stands.
3. **Open candidate 1: large one-day moves, split by whether news came with them.** Two long-only legs: a big up-move with news (continuation) and a big down-move without news (reversal). Tier 1 papers on 1980-2015 data find drift after news shocks and reversal after no-news shocks, both larger in small firms (Chan 2003; Savor 2012; Dyl, Yuksel and Zaynutdinova 2019). But both effects **shrank after decimalization** (Dyl et al.), and no figure after 2015 was found. The count clears easily: about 1,200 effective events a leg over 2019-2023, against 762 needed. Rough pass chance if read: **about 7 to 18 %**, most of it coming from the case where the true gross is at or above 200 bp. Grade **MIXED**.
4. **Open candidate 2: the extreme tail of earnings reactions (top 10 %, not the top quintile), in names from $10M a day up.** This is E1 re-cut to the brief's shape. Going to the tail and adding the $10-200M bands lifts the count to about 1,000 to 1,400 effective events. But the best evidence says slow price formation after earnings is gone outside microcaps since 2006 and in microcaps since 2016 (Martineau 2022, SH-5). The size-screened long leg was +0.07 %/month (B9, SH-6). The plausible gross is 30 to 100 bp a 60-session event. Rough pass chance: **about 5 to 10 %**. The 200 bp shape does **not** change B9's kill or E1's prior.
5. **Ideas that fail on the count, whatever their size:** one-date-a-year events. These are the Russell reconstitution (FC-1) and the January rebound in small tax-loss losers (FC-5: at most about 7 effective events a January, so about 35 over 2019-2023 and about 50 over 2017-2023). S&P 500 deletions fail too, at about 20 a year.
6. **Ideas that fail on size:** the earnings-announcement premium (SH-8: gone since 2004); drift after stock splits (gone since 2000); new 52-week highs (a monthly cross-sectional spread, far under 200 bp an event); high-volume spikes (long side small); short-term losers in tradable names (SH-1, SH-2: lives in illiquid names and bid-ask bounce); S&P 500 deletions (the price effect fell to about −0.6 % in 2010-2020, Greenwood and Sammon 2025).
7. **Ideas that are short-only or outside the tradable bands, ranked last:** lock-up expiries (FC-6: about −0.6 to −1.5 %, mostly under $50M a day), reverse splits (negative multi-year drift, sub-$5 names), extreme short interest and squeezes (short side; squeezes fade within about seven sessions), names that fall below Nasdaq's $1 minimum bid (mostly under $10M a day; INSUFFICIENT), and rebounds after new 52-week lows in the smallest quartile (the published +5 % a week is in the least liquid names; UNVERIFIED).
8. **Not covered:** secondary-offering discounts (the discount goes only to buyers in the offering, and offerings arrive as filings, so they belong to #3687) and fund liquidations (not price-only; HO-8 MIXED).

**One line:** nothing on this list clears both 200 bp and n_eff on evidence. FC-1 has the edge but not the events. Large news-split one-day moves and extreme earnings tails have the events but no evidence of the edge after 2015. The cheapest next step is a counts-only query on the first of these.

## The map (every candidate; gross vs 200 bp, n_eff vs the need)

"Plausible gross" is the long-side drift from the first tradable price after the event (the event-day move excluded). It is net of nothing, and it is our reading of the evidence, UNGRADED. "Need" is the n_eff for 80 % power under E1's rule with a 100 bp round trip, at the candidate's own sd: **n = (1.84 × sd / (gross − 100))²**, which gives 254 at sd 866 (20 sessions) and 763 at sd 1,500 (60 sessions), as in PR #3683. Event names in the $10-200M bands are more volatile than the over-$200M names PR #3683 assumed, so the sd used here is 1,200 to 1,500 bp for 20 sessions and 2,000 bp for 60 sessions (**ASSUMPTION**).

| # | Candidate | Side | Band where it lives | Hold | Plausible gross (bp) | vs 200 | Raw events / yr, tradable | n_eff 2019-23 | n_eff 2017-23 | Need at 200 bp (sd) | vs need | Grade |
|---|---|---|---|---|---:|---|---:|---:|---:|---:|---|---|
| 1 | Extreme earnings reaction / surprise, top 10 % (top 5 %) | long (short side larger) | strongest in microcaps; gone outside them since 2006, in them since 2016 | 60 | 30-100 | **below** | 786 (393) | 980 (490) | 1,375 (690) | 1,354 (2,000) | top 10 % borderline on 2017-23; top 5 % fails | NOT SUPPORTED at 200 bp (SH-6 MIXED, SH-5 NOT SUPPORTED) |
| 2 | Earnings-announcement premium, pre-announcement run-up | long | all | 5 | ≤ 10 | far below | ~7,900 | thousands | thousands | n/a | clears | NOT SUPPORTED (SH-8) |
| 3a | Large one-day move up, with news (continuation) | long | larger in small, low-institutional-ownership firms | 20 | 0-150 | unknown; below on the prior | ~950 | ~1,200 | ~1,650 | 762 (1,500) | **clears** | MIXED |
| 3b | Large one-day move down, no news (reversal) | long | same; bounce risk | 20 | 0-150 | unknown; below on the prior | ~950 | ~1,200 | ~1,650 | 762 (1,500) | **clears** | MIXED |
| 4 | Opening gaps | either | gap reversal strongest in small, illiquid names | 1-20 | unknown | unknown | thousands | ok | ok | — | clears | INSUFFICIENT |
| 5a | New 52-week high | long | robust to size (one source) | 20-60 | < 100 | below | thousands, bunched in rallies | ~500-1,500 | more | 254-1,354 | probably clears | INSUFFICIENT (event form); cross-sectional form replicated |
| 5b | New 52-week low (rebound) | long | smallest quartile | 5 | ~500 a week in the smallest quartile (UNVERIFIED) | above only where untradable | thousands, bunched in sell-offs | high bunching | — | — | band fails | INSUFFICIENT |
| 6 | Extreme volume / attention spike | long (attention reversal is short) | small | 20 | < 100 | below | thousands | ok | ok | 254-762 | clears | MIXED (HO-6, short side) |
| 7 | Short-interest extremes, squeezes | short (low-SI long is small, monthly) | — | 5-20 | long < 50 | below | 24 report dates / yr | heavy bunching | — | — | — | MIXED; data not held |
| 8a | Russell 2000 → 1000 promotions (FC-1) | long | $10-200M | 20 | ~200 (Tier 2, UNVERIFIED) | **at the line** | ~20 | ~35 | ~49 | 487 (1,200) | **fails ×10** | INSUFFICIENT (one Tier 2 source) |
| 8b | S&P 500 deletions (post-deletion rebound) | long | ≥ $200M | 20 | effect −0.6 % in 2010-20; rebound not measured | below | ~20 | ~60 | ~85 | 254 (866) | fails | NOT SUPPORTED (QI-19 family) |
| 9 | Fund liquidations / fire sales | long | — | 60 | ~0 after Wardlaw's correction | below | data late | — | — | — | — | MIXED (HO-8); not price-only |
| 10 | Tax-loss selling, January rebound of small losers (FC-5) | long | small caps | 20 | unknown after 2004 | unknown | 1 date | ~35 | ~49 | 487-762 | **fails ×10** | MIXED |
| 11 | Lock-up expiry (FC-6) | short | 92 % of events under $50M | 3-5 | −60 to −150 | below | ~300 (≥ $10M: ~70) | — | — | — | short | MIXED |
| 12 | Secondary-offering discount | — | — | — | the discount goes to offering buyers | — | — | — | — | — | — | out of scope (#3687) |
| 13 | Post-split drift | long | — | 250 | ~0 since 2000 | below | dozens | small | small | very large (one-year sd) | fails | NOT SUPPORTED |
| 14 | Post-reverse-split drift | short | sub-$5, mostly < $10M | 250 | negative over 1-3 years | short | hundreds (UNVERIFIED) | — | — | — | short; band | MIXED |
| 15 | Extreme 1-month losers in small names (reversal) | long | illiquid names; bid-ask bounce | 20 | 0-50 after a skip day and a mid-session entry | below | thousands | ok | ok | 487 | clears | NOT SUPPORTED in tradable bands (SH-1, SH-2) |
| 16 | Leaving or regaining Nasdaq $1 compliance | either | sub-$1, mostly < $10M | 20-60 | unknown | unknown | hundreds in 2022-23 (UNVERIFIED) | — | — | — | band fails | INSUFFICIENT |

### Ranked shortlist: none clears both lines on evidence

The brief allows up to five ideas that plausibly clear both lines. On the evidence, **none does**. The two below are the only ones where neither line is ruled out: their counts clear, and the size of their effect after 2015 is unknown, not known to be small. **Pass chance** uses PR #3683's closed form for one reading under E1's rule: **P = Φ((g − 100 − max(30, SE)) / SE)**, SE = sd / √n_eff, weighted over a prior on the true gross g. The weights are ours, UNGRADED. A true net of zero (g = 100) already passes 16 % of the time at these counts, so a pass carries little evidence on its own.

| Rank | Candidate | Gross prior (weights) | n_eff, sd | SE | P(pass), one reading | Share of P from g ≥ 200 | Cheapest next check (counts only) |
|---|---|---|---|---:|---:|---:|---|
| 1 | **3a + 3b, large one-day moves split by news**, two long legs, $10-200M, 20 sessions | generous: 60 % at 30, 25 % at 100, 10 % at 200, 5 % at 300 → **18 %**. Harsh: 80 / 15 / 4 / 1 % → **7 %** | 1,000 a leg (conservative against ~1,200), 1,500 bp | 47 | **7-18 %** | about 75 % | Count, in 2019-2023 store bars (with 2017-2018 apart), name-days in $10-200M with an absolute move against the market of at least 10 % on the close-to-close day. Keep the first event per name per 20 sessions. Report distinct dates, events per date (the bunching factor B), the share on the 10 busiest dates, and the share with an 8-K or 2.02 acceptance at [−1, 0] (from the stamps E1 uses). Read the event-day move as the signal; read no return after it |
| 2 | **1, the extreme tail of earnings reactions (top 10 %)**, ≥ $10M, 60 sessions | 70 % at 30, 20 % at 100, 7 % at 150, 3 % at 250 | 1,375 (2017-23), 2,000 bp | 54 | **about 10 %** (5 % at n_eff 980) | about 30 % (the rest is g ≤ 150) | Run E1's existing count code with the floor lowered from $200M to $10M and the cut at the causal p90 instead of p80. Report events by band and B for 2019-2023 and 2017-2023 |

**Nearest misses (each fails one line):**
- **FC-1**, gross ≈ 200 bp, n_eff ≈ 49 against 487: it fails the count. If it were read anyway (sd 1,200, SE 171), it would pass 34 % at g = 200, 16 % at g = 100 and 5.7 % at g = 0. Such a read cannot tell these cases apart, which is why stage 4a (c) defers it.
- **FC-5** (January rebound of small losers): the gross is unknown after 2004, and n_eff is about 49 against 487.
- **Reverse-split short:** a multi-year negative drift, but short-only, sub-$5 and hard to borrow.

**Every other row fails for one reason:**
- 2: gross 10 bp at most.
- 4: no evidence, and the open costs 3 to 5 times mid-session.
- 5a: gross far under 200 bp.
- 5b and 16: the effect sits under $10M a day.
- 6: long side small.
- 7: short side, and the data is not held.
- 8b: gross under 100 bp.
- 9: not price-only.
- 11: short-only, and the names are under $50M.
- 12: out of scope (#3687).
- 13: gone since 2000.
- 15: bid-ask bounce and illiquidity.

## Per-candidate evidence

Each block gives (1) the published drift after the event, (2) evidence after 2015 or after publication, with the grade, (3) where the effect lives by size, (4) the count arithmetic, (5) the side, (6) the matched peer, and (7) the data. Counts use the cost note's band pools: about 1,965 names at ≥ $10M a day and 1,492 at $10-200M ([cost by band](2026-11-02-cost-by-liquidity-band.md), Oct 2026). They assume the bands held about as many names in 2017-2023 (**ASSUMPTION**). **Store caveat:** before 2019 the store holds mostly survivors (36 delisters on file for 2016-07 to 2018, [idea cards](idea-cards.md) source C header). So every 2017-2023 figure includes two years that fail 4a (f) and would be "killed on survivor data".

### 1. Extreme tails of earnings reactions and surprises
1. **Drift.** SUE long-short decile, 1972-2005: about 90 bp/month overall. It is 0.04 %/month in the most liquid Amihud decile and 2.43 %/month in the least liquid, value-weighted, and "transaction costs account for 70-100 percent of the paper profits" (Chordia, Goyal, Sadka, Sadka and Shivakumar, FAJ 2009; abstract via search summary, UNVERIFIED). EAR, CZ value-weighted deciles 2010-2024: long-short 0.81 %/month and a long leg of 0.32 %/month; size-screened long leg 0.07 %/month (SH-6, own computation, [short-horizon note](2026-10-09-short-horizon-candidates.md)). Extreme-return announcers sit in smaller firms, and they reverse in the short term (Aboody et al., RAST, via summary, UNVERIFIED).
2. **After 2015.** Martineau (CFR 2022): no slow price formation outside microcaps since 2006, and in microcaps since 2016 (SH-5, verified in the 10-09 note; the microcap date is from the search summary). Subrahmanyam (SSRN 5930255, 2025): t 2.18 with microcaps, 1.43 without (HO-4). **Grade at 200 bp: NOT SUPPORTED.**
3. **Size.** The drift lives in the least liquid decile. That decile overlaps our < $10M band more than our $10-50M band (**inference**).
4. **Counts.** 1,965 names × 4 announcements × 10 % = 786 a year (5 %: 393). Raw: 3,930 for 2019-23 and 5,500 for 2017-23. E1's correction, n_eff = raw / (2 × B) at B = 2, gives **980 / 1,375** (top 5 %: 490 / 690).
5. **Side.** Long. The short side carries more of the published spread (SH-6: short about 51 bp against long 20 to 35 bp, E1 prior).
6. **Peer.** Yes: size-tercile-matched mid announcers within ±5 sessions (E1's design).
7. **Data.** Held: 8-K 2.02 stamps (E1) and bars. Analyst surprises are not held; the reaction is.
- **Does the shape change B9 or E1?** No. The tail adds count, not edge: SH-6's tail figures are long-short deciles, and the long leg is small. Martineau's microcap result removes the one place a bigger edge might have been.

### 2. Earnings-announcement premium and pre-announcement run-up
1. **Effect.** Value-weighted weekly premium 32.6 bp before 2004 (SH-8). Frazzini and Lamont tie it to volume surges and buying by small investors (NBER w13090, summary).
2. **After 2004.** 0.3 bp (8.7 bp), not different from zero (SH-8, Heitz et al. 2020, verified in the 10-09 note). The paper attributes the loss to the 2004 8-K rules. **NOT SUPPORTED.**
3. to 7. The count is large; the gross is 20 times too small. No size split was found.

### 3. Large one-day moves, with and without news
1. **Drift.** Chan (JFE 2003, 1980-2000): drift after news headlines, reversal after no-news moves (known from earlier repo reading; figures not re-read, UNVERIFIED). Savor (JFE 2012): one-day abnormal moves beyond ±10 %. Moves with analyst reports drift when the report has the same sign; moves without information reverse. A combined strategy earns "35 %" a year annualized (CFA Digest summary, UNVERIFIED; long-short, both legs). Dyl, Yuksel and Zaynutdinova (J. Business Research 2019, 1986-2015): small firms and firms with low institutional ownership are more likely to reverse, and the size of both effects is larger for them; "liquidity improvement following the post-decimalization period led to the reduction in the magnitudes of both" (abstract via summary, UNVERIFIED).
2. **After 2015.** Nothing found. A 2023 master's thesis (Tier 3) on US stocks including COVID finds a reversal that is partly bid-ask bounce. An S&P 500 study (1993-2017) repeats the news/no-news split (summary; the authors were not identified). **MIXED.** The effect is Tier 1 and replicated to 2015, but shrinking, larger in small names, and not net of costs.
3. **Size.** Larger in small, low-institutional-ownership firms. The split between our $10-200M bands is unknown.
4. **Counts (ASSUMPTION: a ±10 % day on 1 % of name-days in $10-200M; half up; half with news).** 1,492 × 252 × 1 % ≈ 3,760 a year, so about 950 a year per long leg. Raw per leg: 4,700 (2019-23), 6,600 (2017-23). Taking the first event per name per 20 sessions and B ≈ 3-4 (crash days such as March 2020 and earnings seasons; raw/n_eff 4 per the brief's range) gives **about 1,200 / 1,650** a leg.
5. **Side.** Both legs that matter are long: continuation after good news and reversal after no-news drops. The short legs are recorded only.
6. **Peer.** Yes: same-date, same-band, size- and volatility-matched names with no large move, from held bars.
7. **Data.** Bars held. "News" needs 8-K acceptance stamps (`filing_events` had 0 rows at the 11-02 census; E1's 2.02 stamps cover earnings only). Analyst reports, Savor's proxy, are not held. A filing-only news proxy is weaker than Savor's, and it is the companion brief's ground (#3687).

### 4. Gaps
- Lou, Polk and Skouras (tug of war, LSE PDF, summary): stocks with high overnight returns keep high overnight and low intraday returns the next month. That is a monthly cross-sectional effect.
- A thesis on US data for 2011-2024 (Turku, Tier 3) finds that large gap-ups give back part of the gain within the session, most in small illiquid names.
- We hold no intraday bars, and the first tradable price after a gap is the open, where cost is 3 to 5 times mid-session ([cost by band](2026-11-02-cost-by-liquidity-band.md), finding 2).
- **INSUFFICIENT**; drift over several days is not studied. It is subsumed by candidate 3 when the event day is defined close to close.

### 5. New 52-week highs and lows
1. **Effect.** George and Hwang (JF 2004): nearness to the 52-week high explains much of momentum, with no long-run reversal. It is a monthly cross-sectional sort. The Chen-Zimmermann replication lists it with original t 2.00 (FEDS 2021-037, summary). Huddart, Lang and Yetman (MS 2009): volume spikes when a 52-week high or low is crossed, larger for small firms. A secondary summary gives about **+5 % in the week after a 52-week low for the smallest quarter** of stocks and +0.6 % after a high (CXO Advisory, Tier 3, UNVERIFIED).
2. **After 2015.** No event-time test found. **INSUFFICIENT** for the event form.
3. **Size.** The large rebound after lows sits in the smallest quartile, which is mostly our < $10M band (**inference**), so it is likely bounce and untradable.
4. **Counts.** Large, but highs bunch in rallies and lows in sell-offs (2020-03, 2022), so B is high. Not computed.
5. to 7. Long. A peer is available. All data held (bars).

### 6. Extreme volume and attention spikes
- Gervais, Kaniel and Mingelgrin (JF 2001, data to the mid-1990s): stocks with unusually high volume over a day or a week rise over the next month. The size is not recovered here (UNVERIFIED), and no post-publication test was found.
- Attention reversal is the short side: HO-6, −4.7 % over 20 days for top Robinhood buys, 2018-2020. The data ended in 2020.
- **MIXED.** The long side is small. The count clears. Volume is held.

### 7. Short-interest extremes and squeezes
- Boehmer, Huszár and Jordan (JFE 2010): heavily traded stocks with low short interest earn positive abnormal returns. This is a monthly sort, liquid names.
- Hong, Li, Ni, Scheinkman and Yan (NBER w21166): days-to-cover long-short about 1.2 %/month, sample not seen (UNVERIFIED).
- Squeezes "fade within seven trading days" (FAJ 2023, summary).
- Long-side gross is far under 200 bp an event. **MIXED; short-only in the form with any size.**
- **Data (not held).** FINRA equity short-interest files are free and twice-monthly, by settlement date. Pitfalls:
  - Publication comes several business days after settlement (exact lag UNVERIFIED), so `known_at` must be the publication date.
  - The report is a snapshot that misses intra-period covering.
  - History for exchange-listed names before FINRA's own series may sit with the exchanges (UNVERIFIED).

### 8. Index deletions and migrations
- **S&P 500.** The deletion effect averaged −4.6 % in the 1980s, about −16 % in the 1990s, −12 % in 2000-2009 and **−0.6 % in 2010-2020**. More deletions now migrate to the MidCap 400 (Greenwood and Sammon, JF 2025, via summary; QI-19). About 20 a year, so the count fails as well.
- **Russell (FC-1).** Stocks migrating out of the R2000: −1.1 % over the 20 days before reconstitution, a rebound of **about 2 % over the 20 days after**, and volume 12 times normal on the day (Dimensional, 2014-2023, Tier 2, via summary). 18 to 19 names a year in some years. **INSUFFICIENT** (one Tier 2 source).
- **Count.** FC-1's card and review give about 7 effective events a June date (ρ 0.1). So about 35 for 2019-23 (5 dates) and 49 for 2017-23. Semi-annual dates start in December 2026, after the window.
- **Peer.** Size-matched stayers (Dimensional's design). The 200 bp shape does not change FC-1's `deferred: power`.

### 9. Fund liquidations and fire sales
HO-8 MIXED. After Wardlaw's (JF 2020) correction the fall is negligible, with no reversal. 13F and N-PORT data arrive weeks late. Not price-only; out of this brief's shape.

### 10. Tax-loss selling and the January rebound (FC-5)
- Haug and Hirschey (FAJ 2006, data to 2004): the small-cap January effect persists after 1986, which weakens the tax explanation.
- Yan (2014) finds little role for institutional tax-loss selling; Sikes (2014) finds one (both via summary).
- An NC State piece (2024, Tier 2/3) calls the effect "small or non-existent" over 2000-2023.
- **MIXED.** No post-2005 US figure for prior-year small losers was found (the FC-5 review found none either). The tail-and-band shape could lift the gross toward 200 bp, but it cannot lift the count: there is one date a year with shared small-cap moves.
- **Count.** ≤ 7 effective events a January (FC-5 card, ρ 0.15), so 35 for 2019-23 and 49 for 2017-23, against 487-762. **Fails ×10.**

### 11. Lock-up expiry (FC-6)
- Field and Hanka (JF 2001): −1.5 % over three days.
- Theses on 2000-2020 US data (Tier 3) find no day-of drop but −0.58 % (3 days) and −0.94 % (5 days) before expiry. Lock-ups longer than 180 days show positive returns (2001-2011 sample).
- Short-only. 998 of 1,080 events are under $50M a day (FC-6 review). **MIXED.** Ranked last.

### 12. Secondary-offering discounts
The discount is earned by buying at the offer price, which a retail account cannot do. The event arrives as a filing (424B, 8-K). Left to #3687.

### 13. Post-split drift
- Ikenberry, Rankine and Stice (1996): +7.93 % in the first year, 1975-1990 (via summary).
- Byun and Rozeff (JF 2003): small or negligible in calendar time, 1927-1996.
- Boehme and Danielsen (FR 2007): no drift after the split date itself.
- Drift "focused in 1975-1999 … no drift from 2000-2011" (AUT working paper, summary).
- **NOT SUPPORTED.** Also, the one-year horizon carries an sd several times larger than a 60-session hold's, so the count needed is out of reach.

### 14. Post-reverse-split drift
- Kim, Klein and Rosenfeld (RQFA 2008) and an EFMA 2006 paper: about 1,600 firms with significantly negative 1- to 3-year returns, only in the lower-priced subsample.
- Dimkpah and Ogwu (IAER 2012): negative at months 1-12, positive at months 13-36 (summary).
- Nasdaq's own commentary attributes the drift to weak fundamentals (Tier 2).
- **MIXED, short-only.** The names are sub-$5, mostly under $10M a day, and hard to borrow.

### 15. Extreme one-month losers in small names
- Reversals are strongest in illiquid stocks with high turnover, and costs remove most of the profit (Avramov, Chordia and Goyal, JF 2006, summary).
- Bid-ask bounce drives Nasdaq reversal in the 1980s (Kaul and Nimalendran, via summary).
- Post-decimal REV is 18 bp/month (SH-1), and costs exceed reversal spreads (SH-2).
- **NOT SUPPORTED in the tradable bands.** The long-horizon form (3- to 5-year losers) holds for years and was not searched.

### 16. Leaving or regaining exchange minimum-price compliance
- A Nasdaq bid-price failure is 30 consecutive business days below $1.00, followed by a 180-day compliance period (Nasdaq rule filings, Tier 1). The 2025 rule change limits second periods.
- Rhee and Wu (JFM 2012) evaluate the rule; their findings were not retrieved.
- **INSUFFICIENT.** The names are mostly under $10M a day (inference from the cost note: names under $2M are almost all under $5).
- **Data.** Nasdaq's daily noncompliant-company list is live only, with no history found. History can come from 8-K Item 3.01 (#3687), or from a close-below-$1 proxy on held bars (closes, not bids).

## Disconfirmation
Searches run (all 2026-11-20). For each: what was sought, and what was found against.
- **PEAD in liquidity deciles, PEAD after 2015 by size, Martineau's microcap date:** found against. The drift is gone outside microcaps since 2006 and in microcaps since 2016, and costs take 70 to 100 % (Chordia et al.).
- **Major price shocks (Savor), large moves after 2010, extreme one-day moves 1986-2015:** found against. The effects shrank after decimalization, and the reversal is partly bounce.
- **52-week high post-publication, 52-week lows:** no decay study found. The large rebound after lows is in the smallest quartile.
- **S&P deletions and the disappearing index effect:** found against (−0.6 % in 2010-2020).
- **Turn-of-year decay, "January effect dead":** found against, Tier 2/3 only. No peer-reviewed post-2005 figure.
- **Reverse splits and the $1 rule:** mixed signs. Short side.
- **High-volume premium replication:** nothing post-publication.
- **Short interest after 2010, squeezes:** squeezes are short-lived.
- **Reversal and bid-ask bounce in small stocks:** found against (bounce and costs).
- **Post-split drift decay:** found against (gone since 2000).
- **Extreme EAR tails after 2010:** nothing found.
- **Russell deletions:** FC-1's +2 % found again (Tier 2).
- **EA premium after 2004:** found against (SH-8 again).
- **Gaps:** intraday reversal in small names.
- **Lock-ups after 2010:** weakened (theses only).
- **Nasdaq noncompliant list:** live only.

Nothing found argues that any of these events got **larger** after 2015 in names from $10M a day up. The one search that might have (UCLA Anderson's "Is PEAD a thing again", a summary of Subrahmanyam) could not be opened. Its search snippet reports the microcap-dependence finding, which cuts against the idea.

## Caveats
- **Every literature figure is from a search summary or an earlier repo report.** Two full-text hosts (`cfr.ivo-welch.info`, `anderson-review.ucla.edu`) were outside the session's network allowance, and no further fetches were tried. No figure here is page-verified. Each one is UNVERIFIED unless the row cites a repo report that verified it.
- **The counts are arithmetic on assumptions, not store counts.** The weakest assumption is the 1 % frequency of ±10 % days for candidate 3. If it is 0.3 %, n_eff per leg falls to about 360 (2019-23), under the 762 needed at sd 1,500. That is why the first next check is the count.
- **The sd figures are assumptions** (1,200 to 1,500 bp for 20 sessions, 2,000 for 60, in small event names). n scales with sd², so an sd 1.4 times larger doubles the need.
- **The pass chances use our prior weights**, UNGRADED, and one reading. At these counts a true net of zero passes 16 %, so about a quarter of candidate 3's chance is a pass that would mean little. A read that passed would still face the exam: an event book at 200 bp gross, sd 1,500 and 300 effective events a year has a net IR of about 1.15 (PR #3683), against about 1.6 for an even chance at the exam.
- **2017-2023 includes 2017-2018**, which fail 4a (f) on delister coverage. The usable development window per the idea cards is 2020-08-31 to 2023-12-29, which is shorter than either column.
- **Defining an event from the event-day move reads a price change.** We treat it as a signal input (as FC-5's card does), not an outcome. Whether the development boundary allows it for a counts query is for the owner or reviewer to confirm.

## What is missing (why the status is INCOMPLETE)
- Page-level verification of every cited figure (Savor 2012, Chan 2003, Dyl et al. 2019, Chordia et al. 2009, Greenwood and Sammon 2025, Dimensional 2024, Huddart et al. 2009, Gervais et al. 2001, the split and reverse-split papers).
- Savor's per-event drift by leg and horizon, which decides whether candidate 3's prior should sit nearer 200 bp than 50 bp.
- Any post-2015 figure for candidates 3, 5, 6 and 10.
- The issue text of #3688 itself: its budget line was not seen.

## Follow-up questions (not answered here)
- Do the 8-K stamps the companion brief (#3687) covers reach back far enough to split candidate 3's events by news? If not, is the earnings-day-only split (E1's 2.02 stamps) a useful first cut?
- Should stage 4a (c) carry a power line at the idea's own sd, as PR #3683's option 5 proposes, so that a small-name event idea is not judged at a large-cap sd?

## Evidence summary
| Claim | Source | Tier | Key figure | OOS / post-pub? | Net of costs? |
|---|---|---|---|---|---|
| SUE drift lives in illiquid stocks; costs take most of it | Chordia et al., FAJ 2009 | 1 (abstract, UNVERIFIED) | 0.04 vs 2.43 %/mo; costs 70-100 % | no (to 2005) | yes |
| Drift gone outside microcaps since 2006, in microcaps since 2016 | Martineau, CFR 2022 | 1 | SH-5 | yes | gross |
| News shocks drift, no-news shocks reverse | Savor, JFE 2012; Chan, JFE 2003 | 1 (summary, UNVERIFIED) | 35 %/yr annualized, combined L/S | no | no |
| Both shrink after decimalization; larger in small firms | Dyl et al., JBR 2019 | 1 (abstract, UNVERIFIED) | direction only | yes (1986-2015) | no |
| S&P 500 deletion effect −0.6 % in 2010-2020 | Greenwood and Sammon, JF 2025 | 1 (summary, UNVERIFIED) | −0.6 % | yes | gross |
| R2000 migrating names rebound about 2 % over 20 days | Dimensional 2024 | 2 (summary, UNVERIFIED) | +2 %, 2014-2023 | yes | gross |
| Post-split drift gone after 2000 | Boehme and Danielsen 2007; AUT WP | 1 (summary) | none from 2000 to 2011 | yes | gross |
| Reverse splits: negative 1-3 year drift, low-priced | Kim, Klein and Rosenfeld 2008; EFMA 2006 | 1 (summary) | direction only | partly | gross |
| January small-cap effect persisted to 2004 | Haug and Hirschey, FAJ 2006 | 1 | direction only | to 2004 | gross |
| Squeezes fade within seven sessions | FAJ 2023 | 1 (summary) | 7 days | n/a | no |
| EA premium gone since 2004 | Heitz et al. 2020 | 1 | SH-8 | yes | gross |

## Proposed claims (for `claims.toml`; not added here)
| Proposed id | Statement | Kind | Grade | Scope |
|---|---|---|---|---|
| PE-1 | No price-, volume-, earnings-number- or supply-date-defined US equity event was found with published post-2015 evidence of a long-side drift of ≥ 200 bp from the first tradable price in names trading ≥ $10M a day. | empirical (search result) | INSUFFICIENT | 16 candidate families, 27 searches, 2026-11-20; summaries only |
| PE-2 | Price continuation after large one-day moves with news and reversal after large moves without news are larger in small, low-institutional-ownership firms and shrank after decimalization (1986-2015). | empirical | MIXED | Savor 2012, Chan 2003, Dyl et al. 2019; long-short, gross, pre-2016 |
| PE-3 | The average S&P 500 deletion price effect fell from −12 % in 2000-2009 to −0.6 % in 2010-2020. | empirical | UNGRADED (one source, read from a summary) | Greenwood and Sammon, JF 2025 |
| PE-4 | Post-split abnormal drift is absent after 2000 when measured from the split date. | empirical | NOT SUPPORTED | Boehme and Danielsen 2007; Byun and Rozeff 2003; AUT WP; summaries only |
| PE-5 | Events that occur on one calendar date a year (Russell June reconstitution, the January tax-loss rebound) cannot reach n_eff 255 in a 2017-2023 window: at about 7 effective events a date they give about 49. | arithmetic | UNGRADED | ρ 0.10-0.15 assumed (FC-1, FC-5 cards) |

## Sources
All accessed 2026-11-20 through web-search result summaries. None was opened in full. Tier as listed.
1. Chordia, Goyal, Sadka, Sadka, Shivakumar (2009), Liquidity and the post-earnings-announcement drift, FAJ 65(4). https://rpc.cfainstitute.org/research/financial-analysts-journal/2009/liquidity-and-the-post-earnings-announcement-drift (T1, abstract)
2. Martineau (2022), Rest in Peace Post-Earnings Announcement Drift, CFR 11. https://ideas.repec.org/a/now/jnlcfr/104.00000122.html ; full text https://cfr.ivo-welch.info/published/papers/martineau2021rest.pdf (blocked) (T1)
3. Subrahmanyam (2025), SSRN 5930255, via https://anderson-review.ucla.edu/is-post-earnings-announcement-drift-a-thing-again (blocked; snippet only) (T1 via T2)
4. Savor (2012), Stock returns after major price shocks: the impact of information, JFE 106(3). https://econpapers.repec.org/RePEc:eee:jfinec:v:106:y:2012:i:3:p:635-659 ; digest https://rpc.cfainstitute.org/research/cfa-digest/2013/02/stock-returns-after-major-price-shocks-the-impact-of-information-digest-summary (T1; T2 digest)
5. Dyl, Yuksel, Zaynutdinova (2019), Price reversals and price continuations following large price movements, J. Business Research 95. https://ideas.repec.org/a/eee/jbrese/v95y2019icp1-12.html (T1, abstract)
6. Greenwood, Sammon (2025), The disappearing index effect, JF 80(2). https://www.nber.org/system/files/working_papers/w30748/w30748.pdf ; https://alphaarchitect.com/disappearing-index-effect/ (T1; T2)
7. Dimensional, Even migrating stocks face index reconstitution costs. https://www.dimensional.com/us-en/insights/even-migrating-stocks-face-index-reconstitution-costs (T2)
8. George, Hwang (2004), The 52-week high and momentum investing, JF. https://www.bauer.uh.edu/tgeorge/papers/gh5-paper.pdf ; Chen-Zimmermann replication FEDS 2021-037 https://www.federalreserve.gov/econres/feds/files/2021-037pap.pdf (T1)
9. Huddart, Lang, Yetman (2009), Volume and price patterns around a stock's 52-week highs and lows, MS 55(1). https://ideas.repec.org/a/inm/ormnsc/v55y2009i1p16-31.html ; summary https://www.cxoadvisory.com/technical-trading/trading-after-52-week-highs-and-lows/ (T1; T3 summary)
10. Gervais, Kaniel, Mingelgrin (2001), The high-volume return premium, JF 56(3). https://econpapers.repec.org/RePEc:bla:jfinan:v:56:y:2001:i:3:p:877-919 (T1)
11. Boehmer, Huszár, Jordan (2010), The good news in short interest, JFE. https://ink.library.smu.edu.sg/lkcsb_research/4664 ; Hong et al., Days to cover and stock returns, NBER w21166 https://www.nber.org/papers/w21166.pdf ; Short squeezes, FAJ 2023 https://rpc.cfainstitute.org/research/financial-analysts-journal/2023/short-squeezes (T1)
12. FINRA equity short interest data glossary. https://www.finra.org/finra-data/browse-catalog/equity-short-interest/data/glossary (T1, official)
13. Haug, Hirschey (2006), The January effect, FAJ 62(5). https://ideas.repec.org/a/taf/ufajxx/v62y2006i5p78-88.html ; NC State (2024) https://mba.ncsu.edu/2024/12/17/does-the-stock-market-feel-the-holiday-spirit (T1; T2/3)
14. Boehme, Danielsen (2007), Stock-split post-announcement returns, Financial Review 42(4). https://ideas.repec.org/a/bla/finrev/v42y2007i4p485-506.html ; Byun, Rozeff (2003) JF https://ideas.repec.org/a/bla/jfinan/v58y2003i3p1063-1085.html ; AUT working paper https://acfr.aut.ac.nz/__data/assets/pdf_file/0005/105476/89582-Splits-Longrun-2017.pdf (T1)
15. Kim, Klein, Rosenfeld (2008), Return performance surrounding reverse stock splits, RQFA 30(3). https://ideas.repec.org/a/kap/rqfnac/v30y2008i3p253-279.html ; EFMA 2006 https://www.efmaefm.org/0efmameetings/EFMA%20ANNUAL%20MEETINGS/2006-Madrid/papers/568563_full.pdf ; Dimkpah, Ogwu (2012) IAER https://ideas.repec.org/a/kap/iaecre/v18y2012i4p439-44910.1007-s11294-012-9370-3.html ; Nasdaq https://www.nasdaq.com/articles/the-impact-of-reverse-splits-on-low-priced-stocks-2021-10-28 (T1; T2)
16. Avramov, Chordia, Goyal (2006), Liquidity and autocorrelations in individual stock returns, JF 61(5). https://ideas.repec.org/a/bla/jfinan/v61y2006i5p2365-2394.html (T1)
17. Lou, Polk, Skouras, A tug of war. https://personal.lse.ac.uk/polk/research/TugOfWar.pdf ; Turku thesis https://www.utupub.fi/bitstreams/88057d2a-1ee8-4c05-9592-c535a21b502c/download (T1; T3)
18. Frazzini, Lamont, The earnings announcement premium and trading volume, NBER w13090. https://nber.org/papers/w13090 ; Heitz et al. (2020) https://www.iimb.ac.in/ARC2020/Papers/The_Disappearing_Earnings_Announcement_Premium.pdf (T1)
19. Lock-up theses: https://thesis.eur.nl/pub/60187/Bachelor-Thesis-Final-Version-498076ll.pdf ; https://ink.library.smu.edu.sg/etd_coll_smu/51/ (T3)
20. Nasdaq noncompliant list https://listingcenter.nasdaqomx.com/NonCompliantCompanyList.aspx ; bid-price compliance rule change, Federal Register 2025-01-23 https://www.govinfo.gov/content/pkg/FR-2025-01-23/html/2025-01621.htm ; Rhee, Wu (2012) JFM 15(2) (cited in summaries; not retrieved) (T1, official)
21. Repo: PR #3683 (pass arithmetic, not yet on main); [short-horizon candidates](2026-10-09-short-horizon-candidates.md) (SH-1 to SH-8); [B9 kill](2026-10-18-b9-kill-b3-retirement.md); [E1 design](2026-11-16-e1-earnings-reaction-design.md); [candlesticks](2026-10-20-candlestick-patterns.md); [cost by band](2026-11-02-cost-by-liquidity-band.md); [idea cards](idea-cards.md) (FC-1, FC-5, FC-6); [research program §4a](../ways-of-working/research-program.md); `claims.toml` (QI-2, QI-19, HO-4, HO-6, HO-8, SH-1 to SH-8, LS-6).
