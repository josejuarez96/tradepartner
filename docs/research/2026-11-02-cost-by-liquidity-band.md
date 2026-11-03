# Research Report: Trading cost by liquidity band at our order sizes (a quote-based feasibility study, no returns)

**Brief:** #2919  ·  **Date:** 2026-11-02  ·  **Status:** COMPLETE for a 100-name sample over 10 sessions (2026-10-19 to 10-30); quote-based, not the rung-1 rule's paper-fill measure  ·  **Agent/model:** team rescostband, claude-opus-5-5; read-only close × volume levels from a store copy, one paced Alpaca SIP pull of quotes and trades. No return, price change, backtest or order

## Answer
**Verdict:** SUPPORTED, provisionally: two bands under $200M are cheap enough mid-session for edges of the cards' size, and one is not  ·  **Confidence:** medium on the quoted figures (100 names, 10 sessions, one month); low on any live fill cost (no fills measured, impact modelled)

1. **Yes, if the trade waits until mid-session.** From 10:00 to 15:30 the quoted half-spread averages **+11.9 bp** in $50-200M names (95% over names +8.9 to +15.0) and **+16.0 bp** in $10-50M names (+12.2 to +20.1). Over $200M it is +6.1 bp. Trades on the tape paid less: **+6.6** and **+8.8 bp** effective half-spread, against +3.8 bp over $200M. Under $10M the quoted half-spread is **+83 bp** (+55 to +113) and the effective one +47.5 bp.
2. **The open is where the cost is.** At 09:35 the quoted half-spread is 3 to 5 times its mid-session level in every band: +22 (≥ $200M), +57 ($50-200M), +87 ($10-50M) and +233 bp (< $10M). At 10:00 it is about twice the mid-session level. The paper simulator's arrival half-spreads (+20 / +46 / +116 bp, buys sent 6 to 10 minutes after the open, [10-30 note](2026-10-30-paper-fill-cost-arrival.md)) match the 09:35 figures here. So the paper book's high cost below $200M comes from **when** it trades as much as **what** it trades.
3. **At our sizes, depth is not the binding limit from $10M up.** A $500 order fits inside the displayed touch on 99 to 100% of mid-session snapshots from $10M up, and a $5,000 order on 73% ($10-50M), 82% ($50-200M) and 99% (≥ $200M). Under $10M, $500 fits 87% of the time and $5,000 fits 26%. With the conservative book model (levels of the touch size, one full spread apart), a $5,000 order costs **+13.4 bp** at $50-200M and **+32.0 bp** at $10-50M (median name +14.0), against **+706 bp** under $10M (median +96).
4. **Per card under D9** (twice break-even), using the mid-session cost of a $500 order and the store's split of each card's events by band:
   - **IC-B3**: passes at $50-200M. $10-50M sits on the margin line. Under $10M it fails.
   - **FC-6**: before borrow, the same pattern as IC-B3.
   - **FC-1**: passes at +2.0% in the bands where its events sit (97 to 99% are $10-200M, not under $10M as the review's split implied). At +1.0% it passes at $500 orders and sits on the margin at $5,000.
   - **FC-5**: passes at $10M and up with $500 orders. Under $10M it falls in the margin band.

   No card is admitted by this note: each still has another block, and the rung-1 rule needs paper fills in band.

**One line:** names of $10M to $200M of daily dollar volume, traded after 10:00 at $50 to $5,000 an order, cost about 12 to 17 bp a side quoted (7 to 9 effective), which leaves room for edges of 50 bp and more a round trip. Names under $10M, and the first half hour in every band, do not.

## Evidence

### Sample, pinned before the draw
The method was pinned in `METHOD.md` and posted on #2919 before the draw or any pull. Deviations D1 to D5 are at its foot and under Caveats.
- **Universe.** Every security whose latest classification is `common`, whose latest non-retracted listing is on NASDAQ, NYSE or NYSE American, with a plain ticker (1 to 5 letters, unique to one security). It must have bars on at least 18 of the 20 sessions 2026-09-21 to 10-16, a bar on 10-16, and a 10-16 close of at least $1.
- **Band.** The median close × volume over those 20 sessions, at the latest `known_at` revision of each bar: < $10M (pool 1,490), $10-50M (828), $50-200M (664), ≥ $200M (473).
- **Draw.** In each band, the first 25 tickers in `md5('costband-2919:' || ticker)` order. That gives 100 names, 84 of them at or above the universe's $5 floor (13 of the 25 under $10M).
- **Sessions.** 2026-10-19 to 10-30, 10 sessions, 1,000 name-sessions, every one pulled.

### Pull
Alpaca SIP, through the repo's `adapters.alpaca_raw._stock_data_client`, paced at 0.3 s per request.
- **Quote snapshots:** the NBBO at New York times 09:30:30, 09:35, 10:00, 11:00, 12:00, 13:00, 14:00, 15:00, 15:30 and 15:55. The NBBO at t is the newest quote in the 30 minutes before t (for the two opening snapshots, since 09:30:00) with bid > 0, ask > 0 and ask ≥ bid.
- **Trade windows:** the trades and quotes in the 120 s after 09:35, 12:00 and 15:30, with each trade matched to the prevailing quote.
- **Volume:** 9,946 valid snapshots and 101,163 regular trades (conditions within `@ I F E`).
- **Units:** quote sizes are in shares. FIX, at $1,400, shows 10-share touches, so the round-lot reform's smaller lots are visible.

### Quoted half-spread, bp
Means are over names (each name's mean across its sessions), with 95% intervals from 2,000 bootstrap resamples of names. The figure after the slash is the median over name-snapshots.

| Band | Mid-session (10:00 to 15:30, 7 points) | median | 09:30:30 | 09:35 | 10:00 | 15:30 | 15:55 |
|---|---|---|---|---|---|---|---|
| < $10M | **+83.4** (+55.1 to +113.0) | +40.2 | +284 / +221 | +233 / +200 | +140 / +106 | +67 / +36 | +61 / +30 |
| $10-50M | **+16.0** (+12.2 to +20.1) | +11.6 | +116 / +92 | +87 / +63 | +33 / +25 | +11 / +8 | +12 / +9 |
| $50-200M | **+11.9** (+8.9 to +15.0) | +8.0 | +74 / +60 | +57 / +39 | +23 / +19 | +7.5 / +5.4 | +7.6 / +5.1 |
| ≥ $200M | **+6.1** (+4.6 to +7.8) | +4.2 | +37 / +28 | +22 / +15 | +11 / +8 | +4.3 / +3.1 | +4.7 / +2.7 |

Names at or above $5 only: +57.7 (< $10M, 13 names), +14.9, +11.5 and +6.1 bp. Under $10M the band is two populations (post hoc split at $2M, not pinned, deviation D4):
- the 13 names of $2M to $10M: mid-session +32.6 bp (median name +26.7);
- the 12 names under $2M: +138 bp (median name +133).

### Depth at the touch and whether our orders fit
Touch dollars: ask size × ask on the buy side, bid size × bid on the sell side. Both sides are pooled.

| Band | Median touch, mid-session | p10 | p90 | $500 fits (mid-session) | $5,000 fits (09:35 / 10:00 / mid-session) | Sessions where $5,000 fits at all 7 mid-session points, both sides |
|---|---|---|---|---|---|---|
| < $10M | $2,423 | $405 | $9,948 | 87% | 17% / 16% / 26% | 1% |
| $10-50M | $9,359 | $2,559 | $31,450 | 99% | 54% / 61% / 73% | 36% |
| $50-200M | $15,268 | $4,195 | $79,415 | 100% | 78% / 78% / 82% | 63% |
| ≥ $200M | $17,472 | $7,458 | $53,952 | 100% | 98% / 97% / 99% | 94% |

A $50 order fits inside the displayed touch on every snapshot in every band. The NBBO shows only round lots, so a quote is never smaller than one lot. A $500 order misses the touch only in names under $10M, almost all of them priced under $5: at or above $5, $500 fits 100% of the time in every band.

### Conservative per-side cost, bp
- **Book model:** the displayed touch repeats at every level, one full quoted spread apart, so the k-th touch-sized chunk pays the half-spread plus (k − 1) spreads.
- **Unit:** the cost is the notional-weighted mean over chunks, averaged over the buy and the sell side.
- **What it leaves out:** no volatility or price-change term, since none can be read without a return. Levels past the first are assumed, not seen.

| Band | When | $50 | $500 | $5,000 |
|---|---|---|---|---|
| < $10M | 09:35 | +233 (+179 to +285) | +285 (+206 to +365) | +1,420 (+738 to +2,229); median name +462 |
| < $10M | 10:00 | +140 | +189 | +1,173; median +269 |
| < $10M | mid-session | **+83.4** (+55 to +113) | **+112** (+65 to +165); median +41 | **+706** (+295 to +1,210); median +96 |
| $10-50M | 09:35 | +87 (+61 to +114) | +88 | +131 (+90 to +183) |
| $10-50M | 10:00 | +32.5 | +33.0 | +52.6 |
| $10-50M | mid-session | **+16.0** (+12.2 to +20.1) | **+16.6** (+12.3 to +21.8) | **+32.0** (+15.1 to +62.1); median +14.0 |
| $50-200M | 09:35 | +56.7 (+39 to +75) | +56.7 | +63.7 (+45 to +82) |
| $50-200M | 10:00 | +22.6 | +22.6 | +26.0 |
| $50-200M | mid-session | **+11.9** (+8.9 to +15.0) | **+11.9** (+8.9 to +15.0) | **+13.4** (+9.9 to +16.8) |
| ≥ $200M | 09:35 | +21.9 (+15.7 to +28.6) | +21.9 | +22.1 |
| ≥ $200M | 10:00 | +11.1 | +11.1 | +11.3 |
| ≥ $200M | mid-session | **+6.1** (+4.6 to +7.8) | **+6.1** | **+6.2** |

The $10-50M figure at $5,000 is pulled up by a few thin books: NCPL ($2 a share, touch about $1,200) costs +367 bp on this model. Post hoc, $2M to $10M names at mid-session cost +32.7 bp at $500 and +68 bp at $5,000 (median name +54). Under $2M they cost +199 and +1,396 bp.

### Effective half-spread on the tape (the 120 s after 09:35, 12:00 and 15:30)

| Band | Trades | Effective half-spread, bp (95% over names) | Quoted half-spread at those trades | Ratio | Median trade |
|---|---|---|---|---|---|
| < $10M | 10,462 | +47.5 (+26.0 to +73.8) | +88.3 | 0.54 | $154 |
| $10-50M | 26,135 | +8.8 (+7.1 to +10.6) | +24.3 | 0.36 | $531 |
| $50-200M | 30,227 | +6.6 (+5.1 to +8.3) | +17.4 | 0.38 | $1,068 |
| ≥ $200M | 34,339 | +3.8 (+2.9 to +4.8) | +8.7 | 0.44 | $2,488 |

These trades are all regular trades, buyer- and seller-initiated, including midpoint and odd-lot prints. They cluster inside the spread, which is why the ratio is 0.36 to 0.54. Our marketable orders are not a random draw from that flow, so the effective figure is a floor on what we could hope for, not an estimate of our cost. The median trade in the two middle bands, $531 and $1,068, is the size of our orders.

### Against the paper simulator, by band

| Band | Paper context half-spread at arrival (buys 6 to 10 min after the open) | Quoted here at 09:35 | Paper buys vs arrival | Quoted here, mid-session |
|---|---|---|---|---|
| < $50M | +116 | +233 (< $10M), +87 ($10-50M) | +120.6 | +83 / +16 |
| $50-200M | +46 | +57 | +49.2 | +11.9 |
| ≥ $200M | +20 | +22 | +15.6 (rung-1 rule +8.8) | +6.1 |

The paper figures match the 09:35 quotes. The simulator fills a marketable buy between the mid and the ask (the 10-30 note: about 0.6 of the half-spread above the mid). So a paper book trading these bands mid-session should show roughly 0.6 × the mid-session half-spread against arrival: about +7 bp at $50-200M and +10 bp at $10-50M. That is an expectation, not a measurement. The waiting itself has a drift whose sign is untested: the [buy-wait replay](2026-11-02-buy-wait-cost-replay.md) found the open-to-send drift averages about zero over four sessions, with ±100 bp swings between sessions.

### What a limit (passive) order could change, and what it risks
Nothing here was built. All order shapes other than market DAY are refused today (`risk.order_shape_violation`).
- **Gain.** A buy resting at the bid pays no half-spread and may earn some of it. In $10-50M names that is about +16 bp a side mid-session and +87 at 09:35. A marketable limit at the touch caps the cost at the displayed half-spread while the book stays put, which removes the model's beyond-touch term for $5,000 orders.
- **Non-fill.** An event trade with a date (FC-1's implementation close, FC-6's day 180) cannot wait indefinitely. A missed fill is either a missed event or a later market order at a worse price. Under $10M the book moves rarely, so a passive order can sit unfilled for most of the session.
- **Adverse selection.** A resting order fills when someone with news trades through it: it fills on the moves that go against it and misses the ones that go its way. On event names (deal news, lock-up flow) that cost can exceed the half-spread saved. It is not visible in quotes.
- **Paper cannot measure it.** Alpaca's simulator fills a limit order when the NBBO reaches it, with no queue position, so a paper passive book would overstate fills and understate adverse selection (UNVERIFIED for the current simulator version). A passive-execution test would need live fills, or a queue-aware replay on SIP trades.

### The cards, recomputed under D9
- **Rule.** The D9 margin is gross ≥ 2 × break-even, where break-even = sides × cost per side. A card **passes** when its cost per side is at most gross / (4 for a round trip, 2 for one side). It **fails** when the cost is above gross / 2 (round trip) at both the quoted and the effective figure (pinned rule). Between the two it is `deferred: margin`.
- **Cost used.** The mid-session $500 figure, with $5,000 beside it.
- **Event counts by band.** The store's own split, levels only (`events_split.py`), on the pilot review's rules with a `known_at` cut before 2026-10-01. The < $10M rows are split post hoc at $2M.

| Card, gross | Pass if cost/side ≤ | Fail if > | Events by band | < $10M (quoted $500: +112; post hoc $2-10M +33, < $2M +199; effective +47.5) | $10-50M (+16.6; $5,000 +32.0; effective +8.8) | $50-200M (+11.9; $5,000 +13.4) | ≥ $200M (+6.1) | Overall |
|---|---|---|---|---|---|---|---|---|
| **IC-B3** cash merger arb, 56 bp a deal (round trip, the break path; completion costs one side) | 14 (28 one side) | 28 | 2020-08-31..2023-12-29 Form 25 common delistings, band in the 20 sessions before filing (every reason): < $2M 320, $2-10M 129, $10-50M 159, $50-200M 107, ≥ $200M 33, no bar 23 | **fail** (> 28 at both) | **deferred: margin** at $500 (16.6 > 14 quoted; 8.8 effective would clear); passes on the completion path (one side, ≤ 28); at $5,000 still deferred: margin (32 > 28 quoted, not at effective) | **pass** | pass | Cost no longer blocks from $10M up. 299 delistings from $10M up against n_eff 160 (every reason; the cash-deal share is unknown), so power at ≥ $10M turns on the cash share (54% or more needed). Stays `blocked`, now on **data field (deal filings and offer terms)**, the authors' block |
| **FC-6** lock-up expiry, about 60 bp to the short over 3 days, before borrow | 15 | 30 | Non-SPAC common first bars 2020-02..2023-06, band at day 180: < $2M 552, $2-10M 279, $10-50M 167, $50-200M 56, ≥ $200M 26 | **fail** (> 30 at both) | **deferred: margin** (16.6 > 15 quoted; 8.8 effective would clear) | **pass** | pass | 249 events from $10M up against the 180 needed (the proxy is an upper bound, since it includes other new listings), so power may pass. Stays `blocked` on **side short (paper)** and **cost measurement (borrow)**. The borrow on young IPOs is likely the binding cost. The spread is no longer the first block from $10M up |
| **FC-1** R2000→R1000 promotions, +2.0% over 20 sessions (+1.0% with a second haircut) | 50 (25 at +1.0%) | 100 (50) | Ranks 900-1,100 on the rank day, 2021 / 2022 / 2023: < $10M 2 / 7 / 13; $10-50M 142 / 137 / 150; $50-200M 52 / 56 / 38; ≥ $200M 5 / 1 / 0 | at most 6% of events; deferred: margin (112 quoted, 47.5 effective) | **pass** at +2.0% at every size; at +1.0%, pass at $500, deferred: margin at $5,000 | pass | pass | Cost no longer blocks on re-entry (provisional). The review's "72 to 81% under $50M" is almost all $10-50M, not < $10M. The entry is the implementation close: the 15:55 quote is +12.2 bp in $10-50M, but the closing auction is unmeasured. Verdict unchanged: **`deferred: power`** |
| **FC-5** tax-loss January, at most +1.0% | 25 | 50 | No event list in the store (prior-year losers); the card says small caps | +112 quoted, +47.5 effective: **deferred: margin** ($2-10M +33 margin; < $2M +199 quoted, effective not split, likely fails) | **pass** at $500; at $5,000 (+32), margin | pass | pass | Verdict unchanged: **`deferred: power`**. Late-December quotes are not sampled, and tax-loss selling may widen them |

These are cost lines, not verdict changes. [idea-cards.md](idea-cards.md) is not edited here: the measure is quote-based, while spec question 3 names the rung-1 rule's paper-fill figure, so the block that rule implies (`cost measurement (<band>)`) stands until a paper book trades the band. Whether a quote-based figure may stand in for it is the owner's call (see Follow-up).

## Evidence summary
| Claim | Source | Tier | Key figure | OOS / post-pub? | Net of costs? |
|---|---|---|---|---|---|
| Mid-session quoted half-spread by band | Own SIP pull, 100 names × 10 sessions | Own result | +83 / +16.0 / +11.9 / +6.1 bp | Oct 2026 only | n/a |
| The open costs 3 to 5 × mid-session | Own SIP pull | Own result | 09:35: +233 / +87 / +57 / +22 bp | Oct 2026 only | n/a |
| A $5,000 order fits the touch | Own SIP pull | Own result | 26% / 73% / 82% / 99% of mid-session snapshots | Oct 2026 only | n/a |
| Effective half-spread on the tape | Own SIP trades, 101,163 | Own result | +47.5 / +8.8 / +6.6 / +3.8 bp | Oct 2026 only | n/a |
| Paper arrival cost matches the 09:35 quote | [10-30 note](2026-10-30-paper-fill-cost-arrival.md) against this pull | Own result | paper +46 vs quoted +57 ($50-200M); +20 vs +22 (≥ $200M) | Paper only | n/a |
| Events of FC-1 sit in $10-200M; IC-B3's and FC-6's sit mostly under $10M | Store close × volume levels (`events_split.py`) | Own result (counts) | FC-1 97-99% in $10-200M; FC-6 831 of 1,080 and IC-B3 449 of 771 under $10M | n/a | n/a |

## Disconfirmation
- **Searches run:** none new. The argument rests on own data. The 10-25 and 10-30 notes covered Alpaca's paper-fill documents.
- **What was checked against the conclusion:**
  - (a) **Price.** The ≥ $5 subset gives +14.9 and +11.5 bp in the two middle bands, so the result is not driven by cheap stocks.
  - (b) **Medians.** The median name is cheaper than the mean in every band (+11.6, +8.0), so outliers raise the means, not lower them.
  - (c) **Time of day.** At 10:00 the middle bands cost +33 and +23 bp. A card that has to trade at 10:00 loses the pass: IC-B3 and FC-6 drop to `deferred: margin` in both middle bands (33 and 23 bp against pass lines of 14 and 15), and at 09:35 they are above the fail line on the quoted figure (88 and 57 bp).
  - (d) **Paper cross-check.** The 09:35 figures reproduce the paper simulator's arrival half-spreads (+57 against +46, +22 against +20), which supports the pull and the matching.
  - (e) **The < $10M band is not one population.** $2-10M names cost about 2 × the $10-50M band, while names under $2M are hopeless. The pass/fail line for IC-B3 and FC-6 falls near $10M either way.

## Caveats & gaps
- **Not the rung-1 rule's measure.** These are quotes and other people's trades, not our fills. A live marketable order may get price improvement (wholesaler routing) or pay more than the displayed touch; neither is measured.
- **The impact model is assumed:** levels of touch size, one spread apart, with no volatility term. A thin book can be much worse past the touch (under $10M, $5,000 is the median name's +96 bp and can be thousands). For $50 and $500 orders above $10M it hardly matters, since they fit inside the touch.
- **One month, 10 sessions, October 2026.** Spreads vary with volatility regimes. December (FC-5) and event days (deal news, lock-up day 180, reconstitution day) are not sampled, and spreads on event days may be wider or narrower.
- **Event bands are 2020-2023 dollar volumes, costs are 2026 quotes.** A $10-50M name in 2021 is assumed to cost what a $10-50M name costs now.
- **The cards' event counts are proxies.** IC-B3's are delistings for every reason, FC-6's are first bars including non-IPO listings, and FC-1's are a rank window, not a promoted list.
- **Mid-session = 7 points** (10:00, hourly to 15:00, 15:30). Snapshots at equally spaced times estimate the time-weighted mean; 7 points is coarse. Under $10M, 3% of those snapshots had no quote update in 30 minutes and were dropped (D3). Those are quiet books.
- **Data handling.** Two pull workers overlapped on about 29 name-sessions (D2); the duplicates are identical requests. The 20-session window was misdated by one session in the pin and corrected before the draw (D1).
- **Bootstrap scope.** Intervals resample names only, not sessions.

## UNVERIFIED items
- Whether Alpaca's paper simulator gives queue position to limit orders (assumed not).
- Price improvement on Alpaca live marketable orders in these bands.
- Closing-auction cost for FC-1's implementation-close entry.
- Borrow availability and fees for $10-200M recent IPOs (FC-6).

## Follow-up questions (not answered here)
- **Should a quote-based mid-session figure stand in for the rung-1 paper figure for the $10-200M bands** until a paper book trades them? If not, the cheapest way to get the rule's figure is a small paper book (or a tranche of an existing one) that trades $10-200M names after 10:00 for two weeks. That changes execution timing in code and spec, which is out of scope here.
- **Should the main books trade after 10:00?** The paper fill-cost gap below $200M is mostly the opening spread. The drift of waiting is the counterweight, and its sign is untested ([buy-wait replay](2026-11-02-buy-wait-cost-replay.md)).
- **IC-B3:** the cash-deal share of the 299 delistings from $10M up, once deal data is held.

## Sources
- **Store copy** `scratchpad/replay/store-now.duckdb`, `read_only=True`: `classifications`, `listings`, `prices_daily` (close, volume, session, known_at), `delistings` (dates), `facts` (`shares_outstanding`, FC-1's rank day only). No `data/` file and no `.env` were opened.
- **Alpaca SIP** historical quotes and trades through `tradepartner.adapters.alpaca_raw._stock_data_client`.
- **Scripts and outputs** in `~/tradepartner-probes/costband/`, outside the repo, with sha256 prefixes:
  - `METHOD.md` (pinned version `e92c7853`, posted on #2919; with deviations `19b0f735`);
  - `sample.py` (`e4e142e5`), `sample.csv`;
  - `fetch.py` (`a6d903d0`), `raw/` (1,000 files);
  - `analyze.py` (`d60fbfaf`), `analyze.out` (`c0b46248`), `analyze_ge5.out` (`729d100d`), `per_name.csv`;
  - `posthoc_2m.out` (`f5c228c7`);
  - `events_split.py` (`a7e5365f`), `events_split.out` (`8983951a`), `events_split_2m.py`, `events_split_2m.out`.
- **Notes:** [10-30 paper fill cost](2026-10-30-paper-fill-cost-arrival.md), [buy-wait replay](2026-11-02-buy-wait-cost-replay.md), [pilot card review](2026-11-02-pilot-card-review.md), [idea cards](idea-cards.md).
- **Spec:** [idea-pipeline](../specs/idea-pipeline.md) question 3 and D9.
