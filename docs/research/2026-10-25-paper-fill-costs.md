# Research Report: Where the daily book's ~53 bp open slippage comes from, and whether 15 bp per side holds

**Brief:** #2165  ·  **Date:** 2026-10-25  ·  **Status:** COMPLETE for session 2026-10-20 (the only order session in the read-only copy `research-20261021.duckdb`); later span sessions are not in the copy  ·  **Agent/model:** research subagent, claude-opus-5-5; read-only analysis of the store copy plus 4 searches and 4 fetches

## Answer
**Verdict:** MIXED  ·  **Confidence:** high for the arithmetic of the decomposition; medium for "the +53 bp is mostly how paper simulates fills"; low for any live cost level (one session, no quote data, no live fills).

1. **The ~53 bp is slippage against the 10-20 open, not against what the backtest assumes.** Measured against the decision-day close (10-19, which `execution.fill_price = "close"` assumes), the 202 buys cost **0.0 bp** on average (order bootstrap 95% CI −23 to +23). That zero is a coincidence of two large terms that cancel: an **overnight gap of −52.4 bp** (CI −71 to −34) and a **fill-vs-open residual of +52.7 bp** (CI +40 to +67). All orders had the same notional ($494.30), so the notional-weighted and equal-weighted means are the same.
2. **The gap is one market draw, not 202.** SPY gapped only −8.1 bp, and the whole priced universe gapped −5.2 bp on an equal-weighted basis. The book's names (12-1 momentum winners) gapped **−44 bp relative to SPY**. MTUM gapped −77 bp. Over the past year this basket's daily gap relative to SPY has a standard deviation of 48 bp, so 10-20 was an ordinary 1-sigma day for momentum names. It tells us nothing about the average gap.
3. **The residual looks mostly like how paper simulates fills, not like a real execution cost.** Orders filled within 5 s of 09:30 paid about the open (mean −0.9 bp, median 0). Fills from 5 s to 190 s after the open paid +60 to +80 bp, with no gradual build-up. 34 fills (in 12 orders) printed **above the day's SIP high**, by up to 1,050 bp (RUSHB). 154 of the 202 orders were split into 2 to 6 fills spread over three minutes. Alpaca says paper fills are matched against the NBBO quote, without price improvement and with random partial fills. Alpaca says live market orders received before 9:28 are protected at the primary market's opening print. Our orders went in at 08:10 ET.
4. **Recommendation:** keep 15 bp per side as the base cost for close-to-close backtests of liquid names, and keep the sensitivity grid. Do **not** recalibrate costs from paper fills. Model an open-fill strategy as an open-fill backtest (next-session open price), not as close fills with extra bps: about 15 bp per side against the open for names with ADV above about $200M, and 30 bp or more below that. Grading is in the Recommendation section.

## Evidence

### Data and definitions
- Window 5, book `daily`, hypothesis 9 (`momentum-daily-shakedown`, 12-1 momentum, top 20% equal weight). Frozen config: `execution.fill_price = "close"`, `costs.per_side_bps = 15`, sensitivity 0/30/60/100.
- Decisions: 202 `trade` buys, `rebalance_session = 2026-10-19`, `whole_share = false`, $100,000 total. Orders: 202 notional market DAY orders on session 2026-10-20, submitted at 08:10:47 ET (pre-market). Fills: 490 broker-feed fills, none `price_implied` and none superseded, from 09:30:00.05 to 09:33:09.7 ET.
- Prices: `prices_daily` (source `alpaca_sip`, `bar`, raw), taking the latest `known_at` for each security and session. Order price = order VWAP (as in `execution/slippage.py`). bp terms are buy-adverse-positive:
  - total = VWAP / close(10-19) − 1
  - gap = open(10-20) / close(10-19) − 1
  - residual = VWAP / open(10-20) − 1
  - total ≈ gap + residual (the cross term averages −0.25 bp)
- One order (DELL) went ex-dividend $0.63 on 10-20. That is about 5 bp of its own gap, or 0.03 bp of the book's mean, so I left it in.
- The bootstrap is iid over orders (10,000 resamples). **Treat it as a lower bound on the uncertainty.** The orders share one market open, so the session is about one correlated observation.

### Decomposition (n = 202; EW = NW because every order was $494.29 to $494.30)
| Term | Mean bp [95% CI, order bootstrap] | Median | SD |
|---|---|---|---|
| Total vs decision-day close (the backtest assumption) | **0.0** [−22.6, +23.1] | +32.1 | 166 |
| Overnight gap, close(10-19) to open(10-20) | **−52.4** [−70.6, −34.0] | −31.7 | 131 |
| Residual, fill VWAP vs open(10-20) (the "~53 bp" read) | **+52.7** [+39.6, +67.0] | +26.6 | 98 |
| First fill vs open | +55.4 [+41.0, +71.8] | +23.3 | 113 |
| Fill VWAP vs close(10-20) | −79.2 [−109, −49] | −79.7 | 220 |
| Open to close on 10-20, for reference | +137.3 [+108, +168] | +135 | 220 |

### Market context (the "one observation" point)
| Quantity | Value |
|---|---|
| SPY gap, 10-19 close to 10-20 open | −8.1 bp (SPY gap SD: 72 bp since 2016, 54 bp over the last 250 sessions) |
| MTUM gap | −77.1 bp |
| Universe gap, 5,477 names: EW mean / median / dollar-volume weighted / top 1,000 by dollar volume EW | −5.2 / +8.0 / −44.8 / −2.7 bp |
| Book gap minus SPY / minus universe EW | **−44.3 / −47.2 bp** |
| The same 202 names' EW gap minus SPY, over the last ~265 sessions | SD 48 bp; 10-20 z = −1.1; 35% of days had \|rel\| > 44 bp |

The book's −52 bp gap was a moderate momentum-specific down-open on a flat-to-down market open. Days like it happen about once a week, with either sign. Even if every order were independent, the 95% band on one day's gap would be about ±95 bp at session level. One session cannot estimate the mean of the timing term.

### Residual by segment (means in bp; quartiles unless stated)
| Segment | Low group to high group: residual (gap) |
|---|---|
| Open price | $8–61: +63 (0) · $61–122: +83 (−68) · $122–233: +60 (−47) · $233–1,420: **+5** (−96) |
| 20-day dollar ADV | $6M–94M: **+102** (−3) · $94M–181M: +68 · $181M–550M: +47 · $0.55B–27.5B: **−6** (−121) |
| Order / day dollar volume | smallest: +1 · ... · largest (0.0006% to 0.016%): +106 |
| Time of last fill after 09:30 | ≤7 s: **+7** (median 0) · 7–68 s: +54 · 68–118 s: +67 · 118–190 s: **+82** |
| Number of fills | 1: −2 (median 0) · 2: +68 · 3: +60 · 4: +84 · 5–6: +62 |
| Shares bought | <1 share (11 orders): +0.3 · 1–5: +51 · 5–20: +73 · >20 (11): −1.6 |
| Listing exchange | Nasdaq (116): +46 (median 21) · NYSE (86): +61 (median 51) |
| Day range (high/low) | narrow: +64 · ... · widest: +36 |

OLS of the residual on time of last fill, log ADV, gap and log price (R² 0.30):
- log ADV: **−44 bp per log unit** (se 5)
- seconds to complete: +0.43 bp/s (se 0.13)
- log price: +29 (se 8)
- gap: −0.12 (se 0.05)

Liquidity and fill delay are correlated (−0.32), and neither explains everything.

**Fractional vs whole share:** all 202 orders were notional (fractional) orders, so the data has no whole-share comparison. "Shares bought" is the nearest stand-in, and it mostly tracks price and liquidity.

**Order notional:** not a usable segment. Every order was the same size, and orders were 0.00003% to 0.016% of the day's dollar volume, far too small for market impact.

### Fill-level path (490 fills; price vs open, by seconds after 09:30)
| Window | 0–1 s | 1–5 s | 5–15 s | 15–30 s | 30–60 s | 60–90 s | 90–120 s | 120–150 s | 150–200 s |
|---|---|---|---|---|---|---|---|---|---|
| Fills | 13 | 37 | 27 | 31 | 82 | 88 | 91 | 73 | 48 |
| Mean bp | +0.5 | −0.9 | +80.5 | +68.7 | +77.0 | +65.5 | +59.8 | +73.6 | +77.7 |
| Median bp | 0.0 | 0.0 | +41 | +37 | +51 | +49 | +56 | +75 | +75 |

- The step happens between 5 s and 15 s and then stays flat. The market's own drift after the open would grow over time instead. A step like this fits fills moving from the opening print to the ask side of wide early quotes.
- 18% of first fills equal the open to the cent.
- 34 fills printed above the session's SIP high. For example, RUSHB filled at 62.80 against a high of 58.335, and GEF.B at 117.62 against a high of 113.76.

### Alpaca's own description (sources below)
| Claim | Source | Tier | Key figure |
|---|---|---|---|
| Paper fills are simulated from real-time quotes against the NBBO; paper does not model market impact, latency slippage or price improvement; 10% random partial fills, remainder re-evaluated | Alpaca docs, Paper Trading | 1 | "matched against the best available current market price (NBBO)"; does not account for "Price improvement received" |
| Live: market orders are routed to market makers, which send them to the primary's opening auction; orders received before 9:28 are filled at the Nasdaq Official Opening Price (opening-print protection) | Alpaca docs, Orders at Alpaca, "Order Handling Standards" | 1 | "Any market orders received before 9:28 will be filled at the Nasdaq Official Opening Price" |
| Staff: pre-open market orders generally execute "at market open at the 'open' price"; buys typically fill at the NBBO ask; paper fills OPG/CLS at the open/close bid/ask, not the auction price | Alpaca forum, staff replies, Mar 2024 | 2 | quoted in the thread |
| Fractional shares are filled by Alpaca as principal or riskless principal | Alpaca docs, Fractional Trading | 1 | "executed either on a principal or riskless principal basis" |
| Our sandbox: a marketable odd-lot limit filled at the ask, in pieces; OPG is refused inside the session | docs/research/2026-10-19-alpaca-sandbox-probe.md | recorded | 37 sh, filled 12.19 = ask, `partially_filled>filled` |

### Literature on the open and the timing term
| Claim | Source | Tier | Key figure |
|---|---|---|---|
| Momentum profits are earned overnight; intraday partly reverses them | Lou, Polk & Skouras (2019), JFE 134 | 1 | "profits ... for a variety of momentum strategies are earned entirely overnight" |
| NYSE opening-auction trades cost about 20% less than comparable later trades; Nasdaq opening trades cost slightly more; opening quotes show temporary pressure | Darden working paper, NYSE/Nasdaq opens (2001); read as a summary only | 2 | "about 20 percent less costly" (NYSE) |
| Opening auctions are relatively illiquid; auction price impact is lower than continuous trading except for Nasdaq microcaps (closing-auction evidence) | Goyal, Jegadeesh & Wu (JFQA, forthcoming 2026); summary only | 2 | qualitative |

## Disconfirmation
- **Searched for:** evidence that the +53 bp is a real cost, such as market drift right after the open, or a genuine spread that a live order would also have paid.
- **Against my reading:**
  - The book's names rallied +137 bp from open to close, so some of the post-open residual may be real drift.
  - Under CTA/UTP rules, odd-lot trades do not update the consolidated high and low. A real odd-lot buy at a wide ask could print "above the high" too, so those 34 fills do not prove simulation on their own.
  - The residual is largest for the least liquid quartile (ADV under $94M). That is where real opening spreads are also widest.
- **For my reading:** orders that completed in the first 5 s paid the open (median 0). The jump from 0 to about +70 bp is a step within 10 s, not a slope. Alpaca documents opening-print protection for live pre-9:28 market orders and documents the absence of price improvement in paper.
- **Net:** most of the residual is how paper simulates fills, and part of it is real (illiquid names, early spreads). I cannot split the two without quote or minute data. This is why the attribution confidence is medium, not high.

## Recommendation (the owner decides; this is not evidence)
1. **Close-to-close backtests: 15 bp per side stays defensible as the base. Confidence: medium-low.**
   - This session's cost against the assumed close was 0 bp. That is luck: the −52 bp gap offset the +53 bp paper residual.
   - The part that would be real live, a fill near the opening print, is a timing term with an unconditional mean near zero (SPY +3 bp/night since 2016) and a large variance. Spread and impact for orders this small should be well under 15 bp for liquid names.
   - Two cautions. First, for **momentum**, the timing term is probably adverse on average (Lou–Polk–Skouras): buying at the next open gives up the winners' overnight leg. That belongs in the fill-price convention, not in bps. Second, for names with ADV under about $100M, 15 bp is probably thin. Keep the 30/60/100 sensitivity rows and read the 30 bp row for books that hold such names.
2. **Open-fill strategies:**
   - Backtest with the next session's open as the fill price. Do not use the decision close plus a fill-timing allowance: the gap SD (130 bp per name, about 50 bp per basket relative to SPY) cannot be represented by a fixed bp.
   - Assume about 15 bp per side against the open for names with ADV above about $200M, 25–30 bp for $50M–200M, and 50 bp or exclude below $50M. Run 0 and 2× the base as sensitivity.
   - Grade: low-medium, not measured live.
3. **Paper measurement:** paper's slippage against the open (the `paper status` line) measures Alpaca's simulator, not our live costs. Treat it as a pessimistic bound that varies with liquidity. Do not feed it into T210 / kill-ladder rung 1 as a cost level without that label.
   - The daily book will show about +50 bp per traded side of paper residual against a backtest. Expect that in the tracking gap.
4. **Probes that would settle it (new issues, not this one):**
   - (a) A sandbox comparison of whole-share vs notional, and DAY market vs OPG, orders submitted before 9:28 on a few names. Paper fills OPG at the quote, so this needs a tiny live account to show auction-price fills.
   - (b) Store minute bars or the NBBO for 09:30–09:35 on order days, so the residual can be split into spread and drift.
   - (c) Rerun this decomposition on every span session as it arrives. The gap term needs about 20 or more sessions before its mean means anything.

## Caveats & gaps
- One order session (2026-10-20), buys only, one portfolio-build day. No sells, no rebalance-sized orders, no whole-share orders.
- No quote or minute data in the store, so the spread vs drift split inside the residual is inferred, not measured.
- `prices_daily.open` is the SIP daily-bar open. That is close to, but not always the same as, the primary's official opening price that live orders are protected at.
- The basket's historical relative-gap SD uses today's 202 names. That choice is fine for an SD estimate, but the historical mean (+9 bp/day) is biased by selecting on past returns, so I do not interpret it.
- The MTUM gap SD (88 bp) looks high for an ETF. Its open prints may be noisy, so I use it only qualitatively.

## UNVERIFIED items
- Whether Alpaca's opening-print protection applies to **fractional/notional** orders, which Alpaca fills as principal or riskless principal. If it does not, live notional orders could fill like paper's continuous fills.
- The full-text figures of the Darden and Goyal–Jegadeesh–Wu papers (I read only summaries).
- How much of the +53 bp is genuine opening spread for names with ADV under $100M.

## Follow-up questions (not answered here)
- Should the daily book's orders be whole-share, or OPG, to get auction treatment live? This needs the sandbox and live-account probe above.
- Should `paper status` label its open slippage as "paper simulator" so nobody reads it as a cost estimate?

## Sources
- Store copy: `~/Projects/tradepartner-research/research-20261021.duckdb` (read-only), tables `orders`, `fills`, `decisions`, `prices_daily`, `paper_windows`, `listings`, `corporate_actions`. Scripts are in the session scratchpad `cost/` (`main.py`, `fills.py`, `hist.py`).
- Code: `src/tradepartner/execution/slippage.py` (definition of slippage against the open).
- Alpaca, Paper Trading: https://docs.alpaca.markets/docs/paper-trading
- Alpaca, Orders at Alpaca (Order Handling Standards, opening-print protection): https://docs.alpaca.markets/docs/orders-at-alpaca
- Alpaca, Fractional Trading: https://docs.alpaca.markets/docs/fractional-trading
- Alpaca forum, "Frustration with the execution of Pre-Market Orders" (staff replies, Mar 2024): https://forum.alpaca.markets/t/frustration-with-the-execution-of-pre-market-orders/13787
- Lou, Polk & Skouras, "A tug of war: Overnight versus intraday expected returns", JFE 2019: https://personal.lse.ac.uk/polk/research/TugOfWar.pdf
- Darden working paper on NYSE/Nasdaq opening trades: https://www.darden.virginia.edu/sites/default/files/inline-files/Open_2001_WP.pdf
- Goyal, Jegadeesh & Wu, price impact in auctions (JFQA): https://jfqa.org/wp-content/uploads/2026/01/25551_Price_Impact.pdf
- Prior probe: [docs/research/2026-10-19-alpaca-sandbox-probe.md](2026-10-19-alpaca-sandbox-probe.md)
