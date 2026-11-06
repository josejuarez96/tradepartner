# Research Report: Paper fill cost per side against the arrival price, and why buys fill late

**Brief:** #2608  ·  **Date:** 2026-10-29  ·  **Status:** COMPLETE for the three order sessions in the store backup (2026-10-20, 10-26, 10-27); not the two weeks rung 1 asks for  ·  **Agent/model:** team opuscost, claude-opus-5-5; read-only queries on the store backup `tradepartner.duckdb.bak-20261029-1629`, one small Alpaca SIP pull of quotes and minute bars, and a code read of `src/tradepartner/execution/`

## Answer
**Verdict:** SUPPORTED (on paper): the measured per-side cost against arrival is above B10's 12 bp rung-1 line  ·  **Confidence:** medium for the paper figure; low for any live cost (paper simulator, three sessions, mostly tiny orders)

1. **Per side, against arrival, the paper book paid +24.2 bp** (95% order bootstrap +18.9 to +30.6): buys +38.9 bp (+31.9 to +46.8), sells +9.5 bp (+1.8 to +20.2, median 0). The whole interval is above 12 bp. On the two ordinary rebalance days alone (10-26, 10-27) it is +17.0 bp (+12.5 to +22.6), still above. Only orders in names with over $200M of 20-day median dollar volume, measured against arrival and weighted equally by order, come in under the line: +10.6 bp (+6.0 to +15.4), an interval that contains 12. The same names measured against the open, as the run executes today, cost +17.9 bp (+11.3 to +24.4), and notional-weighted +27.3 (arrival) or +28.9 (open): all above 12.
2. **Buys fill late because of how we submit, by design.** The paper run trades in two phases (paper-trading spec req 3, `RiskGatedBroker._trade`): sells go in before the open; buys are submitted only after every sell is terminal (or 900 s after the open), sized from the cash the sells raised. On 10-26 and 10-27 the last sell filled 253 s and 164 s after the open, the run saw that and journaled the sells about 2.5 minutes later, built the buys in about 75 s, and sent them one by one at 1.55 s each. Buys went in 375 to 618 s after the open and each filled about 6 ms after it was sent. The broker did not hold them back.
3. **What submission timing alone would change.** Against arrival, nothing by definition. Against the open, the delay adds a drift term: buys sent after the open drifted +16.4 bp (+2.4 to +30.5) before they were sent: +33.4 and +1.3 bp on the two days, +30.8 bp in names over $200M. It was positive on both days in this sample, and the book buys momentum names, so it may be a real cost of waiting; its sign in general is untested. Paper orders sent before the open and filled in one piece paid about the open (buys -2.3 bp, n 48); split ones paid +70 bp. That is the simulator's partial-fill pattern on the larger 10-20 orders, not evidence that sending buys earlier is cheaper live.
4. **For rung 1:** use **+24.2 bp per side (+18.9 to +30.6)**, against arrival, all orders. It fails 12 bp. The liquid-names figure of +10.6 bp holds only if buys were sent at the open, which needs a change to the spec's cash rule; as the run executes today, liquid names cost +17.9 bp against the open, also above 12. The intervals treat orders as independent; with three sessions they understate the uncertainty.

## Evidence

### Data and definitions
- **Orders.** Book `daily` (window 5, hypothesis 9), sessions 2026-10-20 (202 buys, $494 each, the build day), 10-26 (106 sells, 91 buys) and 10-27 (91 sells, 103 buys): 593 orders, 918 non-superseded fills, every order filled. All are market DAY orders, `whole_share` false; buys are notional (fractional), sells by quantity. Median order size: $494 (10-20 buys), $8 to $23 (10-26 and 10-27). Session 10-29's 82 sells are left out: its run halted on an `AlpacaTradingError` (ConnectionError) before any buy, and the backup holds no 10-29 daily bar.
- **Order price** = VWAP of the order's non-superseded fills (as in `execution/slippage.py`).
- **Arrival price.** The price when the order could first trade:
  - for an order sent **after** the open (every 10-26 and 10-27 buy, 194 orders): the SIP NBBO midpoint of the last quote with a positive bid and ask at or before the order's broker `submitted_at` (from `order_events.raw_json`). Quote age median 2.3 s, max 86 s;
  - for an order sent **before** the open (all sells, the 10-20 buys, 399 orders, sent 08:10 to 08:16 ET): the session's SIP daily-bar open (latest `known_at` revision). The pre-market quote at 08:10 is not a price these orders could get.
- **Terms, bp, buy-adverse-positive** (s = +1 buy, -1 sell): cost vs arrival = s × (VWAP / arrival - 1); vs open = s × (VWAP / open - 1); drift = s × (arrival / open - 1). The three add up up to a small cross term. Drift is zero for a pre-open order by construction.
- **Liquidity band:** median of close × volume over the 20 sessions before the order's session, split at $50M and $200M.
- **Fill delay:** first fill minus 09:30:00 ET.
- **Intervals:** iid bootstrap over orders, 10,000 resamples, 2.5 and 97.5 percentiles. Orders on one session share one open and one market move, so these intervals are a **lower bound** on the uncertainty. "Per side" is the mean of the buy mean and the sell mean, each resampled within its side. Orders are weighted equally, not by notional as `execution/slippage.py` intends for rung 1: notional weights are dominated by the $494 orders of 10-20 and by a few sells (robustness line below gives the notional-weighted figures). The pooled buy figure mixes two references: buys sent after the open are measured from the mid, so they carry the half-spread; buys sent before the open are measured from the open print, wherever it sat in the spread.
- **Reproducibility:** tables are from `analyze.py` (seed 2608), the rung-1 table from `extra.py` (seed 26082), both over orders sorted by `client_order_id`.
- **Check:** the vs-open column reproduces the [10-28 re-derivation](2026-10-28-strategy-frontier-verified.md#fill-audit-re-derived-3-sessions-593-orders-918-fills): buys +46.9 bp, sells +9.5 bp, bands +128 / +51 / +30 against its +131 / +51 / +30 (its <$50M band had 25 buys, as here; the small gap is the band edge).

### By side and by when the order was sent

| Cut | n | vs arrival (95%) | median | vs open (95%) | drift (95%) |
|---|---|---|---|---|---|
| All buys | 396 | **+38.9** (+31.9 to +46.6) | +18.0 | +46.9 (+37.3 to +57.0) | +8.0 (+1.1 to +14.8) |
| All sells | 197 | **+9.5** (+1.7 to +19.9) | 0.0 | +9.5 (+1.8 to +19.9) | 0 |
| Buys sent after the open (10-26, 10-27) | 194 | +24.5 (+20.5 to +29.0) | +15.4 | +40.9 (+26.5 to +55.1) | +16.4 (+2.4 to +30.5) |
| Buys sent before the open (10-20) | 202 | +52.7 (+39.7 to +66.8) | +26.6 | same | 0 |

### By session

| Side, session | n | vs arrival (95%) | vs open (95%) | drift (95%) |
|---|---|---|---|---|
| Buy 10-20 | 202 | +52.7 (+39.6 to +67.1) | +52.7 | 0 |
| Buy 10-26 | 91 | +21.9 (+17.5 to +27.1) | +55.3 (+34.3 to +77.2) | +33.4 (+12.6 to +55.1) |
| Buy 10-27 | 103 | +26.7 (+20.4 to +34.1) | +28.1 (+9.1 to +47.7) | +1.3 (-16.9 to +18.8) |
| Sell 10-26 | 106 | +0.5 (-6.6 to +7.2) | same | 0 |
| Sell 10-27 | 91 | +20.0 (+6.2 to +40.9) | same | 0 |

### By liquidity band (20-day median dollar volume)

| Side, band | n | vs arrival (95%) | median | vs open | drift |
|---|---|---|---|---|---|
| Buy, under $50M | 25 | +120.6 (+63.1 to +194.7) | +71.2 | +128.1 | +7.4 |
| Buy, $50-200M | 196 | +49.2 (+40.2 to +58.6) | +26.5 | +51.4 | +2.2 |
| Buy, over $200M | 175 | +15.6 (+8.9 to +23.0) | +7.5 | +30.2 | +14.6 |
| Sell, under $50M | 15 | +52.1 (-5.6 to +159.4) | 0.0 | same | 0 |
| Sell, $50-200M | 84 | +6.5 (-3.3 to +15.9) | 0.0 | same | 0 |
| Sell, over $200M | 98 | +5.6 (-0.1 to +11.8) | 0.0 | same | 0 |

Buys sent after the open, over $200M: +12.4 bp on 10-26 (n 48) and +11.4 bp on 10-27 (n 35). On 10-20, over $200M, sent before the open: +18.9 bp (n 92).

### By fill delay (first fill after 09:30:00)

| Side, delay | n | vs arrival (95%) | vs open | drift |
|---|---|---|---|---|
| Buy, 5 s or less | 48 | -0.9 (-6.1 to +2.9) | same | 0 |
| Buy, 5-60 s | 94 | +72.2 (+50.4 to +97.9) | same | 0 |
| Buy, 60-300 s | 60 | +64.9 (+44.0 to +85.9) | same | 0 |
| Buy, over 300 s (all sent after the open) | 194 | +24.5 (+20.4 to +29.0) | +40.9 | +16.4 |
| Sell, 5 s or less | 179 | +3.6 (-0.2 to +7.7) | same | 0 |
| Sell, 5-60 s | 5 | +218.3 (+33.3 to +498.7) | same | 0 |
| Sell, 60-300 s | 13 | +10.3 (-37.7 to +50.7) | same | 0 |

Every buy that filled 5 to 300 s after the open is a 10-20 order sent before the open: its delay is the simulator splitting it, not our timing. Orders filled in one piece paid about the open (10-20 buys -2.3 bp, n 48; 10-26 sells -1.0, n 96); orders split into 2 or more fills paid +70 bp (10-20 buys, n 154). On 10-20 the cost fell with liquidity, by quartile of dollar volume: +99, +70, +48, -6 bp, and so did the share of one-piece fills (10%, 12%, 18%, 55%). 16 buy VWAPs sit above the day's SIP high and 5 sell VWAPs below its low, the simulator pattern the [10-25 note](2026-10-25-paper-fill-costs.md) found.

### Where a buy sent after the open fills inside the spread
For the 194 buys sent after the open, all 194 filled between the arrival midpoint and the arrival ask, none outside. The half-spread at arrival averaged +39.4 bp (+33.0 to +47.2; median +25.5): +116 bp under $50M, +46 bp at $50-200M, +20 bp over $200M. The fill sat 14.8 bp below the ask on average (about 0.6 of the half-spread above the mid). Median time from send to fill: 6 ms (max 4.1 s). So on paper, a buy sent after the open costs most of a half-spread six to ten minutes into the session, when spreads are still wide.

### The mechanism of the buy delay

| Step, 10-26 (10-27) | Clock, ET | Source |
|---|---|---|
| Sells journaled `pending`, submitted (106; 91) | 08:11:35 to 08:14:17 (08:10:19 to 08:12:39) | `orders.known_at`, `submitted_at` |
| Sells fill, 94 (85) of them within 5 s of the open | 09:30:00 to 09:34:13 (09:30:00 to 09:32:44) | `fills.filled_at` |
| Sells' fills journaled (collection) | 09:36:45 (09:34:56), 152 s (132 s) after the last sell fill | `fills.known_at` |
| Buys journaled `pending` | 09:37:58 (09:36:14), 73 s (78 s) later | `orders.known_at` |
| Buys submitted one at a time, 1.55 s apart | 09:37:59 to 09:40:18 (09:36:15 to 09:38:52) | `submitted_at` |
| Each buy fills | about 6 ms after its submit | `filled_at` |

From the code:
- **Two phases by design.** Paper-trading spec req 3: "(ii) buys, submitted only after every sell of this run is terminal or `paper.sell_wait_seconds` has elapsed since the open ... buys may therefore land up to that long after the open, by design". `RiskGatedBroker._trade` runs the sells phase, then `_await_sells`, then `_buys`.
- **The wait.** `_await_sells` calls `get_order` on every still-open sell, one at a time, sleeps `paper.poll_interval_seconds` (15 s), and stops when all are terminal or `sell_wait_seconds` (900 s) has passed since the open. Then it runs the T58 collection. The 132 to 152 s between the last sell fill and the journaled collection is this loop plus the collection; the journal does not split the two.
- **The cash rule.** `_buys` sizes from one read of `account().cash` less the open-buy reserve, any unresolved sell proceeds and positive open name-cash breaks, never `buying_power` (spec req 3; `_buys_cash`). So the buys cannot be sized until the sell proceeds are in cash.
- **Serial submits.** Each buy is submitted and then polled with `get_order` (sleeping `paper.poll_interval_seconds` between reads, bounded by `paper.accept_wait_max_seconds`) until the broker knows it, before the next one. That is the 1.55 s per order, so the last of 103 buys goes in about 2.6 minutes after the first.
- **Not the order shape.** Every order is a plain market DAY order (`risk.order_shape_violation` refuses anything else); notional buys were accepted before the open on 10-20, so fractional or notional handling does not force the delay. No buy carried `sells_in_flight_at_submit`, so on both days every sell was terminal before the first buy went in.

So the buy delay (375 to 618 s after the open) is set by our run: the slowest sell's simulated fill (164 to 253 s), plus about 3.5 to 4 minutes of polling, collection, sizing and serial submits. Faster polling or concurrent submits would cut the 3.5 to 4 minutes; sending buys before the open would need sizing them before the sell proceeds exist, which is a change to the spec's cash rule.

### Rung-1 figures (one bootstrap run, seed 26082)

| Sample | Buy (95%) | Sell (95%) | Per side (95%) | Bootstrap share above 12 bp |
|---|---|---|---|---|
| All three sessions | +38.9 (+31.9 to +46.8) | +9.5 (+1.8 to +20.2) | **+24.2 (+18.9 to +30.6)** | 1.000 |
| Rebalance days only (10-26, 10-27) | +24.5 (+20.4 to +29.3) | +9.5 (+1.8 to +19.9) | +17.0 (+12.5 to +22.6) | 0.986 |
| Names of $50M or more | +33.4 (+27.3 to +39.5) | +6.0 (+0.6 to +11.4) | +19.7 (+15.7 to +23.8) | 1.000 |
| Names over $200M | +15.6 (+8.7 to +22.8) | +5.6 (+0.0 to +11.9) | +10.6 (+6.0 to +15.4) | 0.275 |
| Names over $200M, against the open | +30.2 (+18.4 to +42.0) | +5.6 (-0.1 to +11.7) | +17.9 (+11.3 to +24.4) | 0.959 |
| Over $200M, rebalance days only | +12.0 (+10.0 to +14.3) | +5.6 (-0.2 to +11.8) | +8.8 (+5.7 to +12.0) | 0.026 |
| Over $200M, rebalance days, against the open | +42.9 (+23.3 to +63.4) | +5.6 (-0.1 to +11.7) | +24.2 (+13.9 to +34.8) | 0.991 |

Robustness of the per-order means: winsorized at 1% and 99%, buys +37.1 and sells +7.0 bp; each session weighted equally, buys +33.8 and sells +10.3 bp; notional-weighted (the aggregation `execution/slippage.py` names for rung 1), buys +51.7 bp (the $494 orders of 10-20 dominate) and sells +89.1 bp (on $6,820 of sells in total, driven by a few), +70.4 per side (+70.9 against the open). Over $200M, notional-weighted: +27.3 per side against arrival, +28.9 against the open. None of these brings the pooled per-side figure under 12 bp.

**Recommendation for rung 1.** Use +24.2 bp per side (+18.9 to +30.6), against arrival, all orders, equal-weighted: it fails the 12 bp line. B10 trades the top turnover fifth, about 40 names a month; whether those are mostly over $200M of daily dollar volume is not measured here. Even if they are, the +10.6 bp (+6.0 to +15.4) liquid-names figure leaves out the buy drift and holds only if buys were sent at the open, which needs a change to the spec's cash rule; as executed today those names cost +17.9 bp (+11.3 to +24.4) against the open, and +27.3 to +28.9 bp notional-weighted, all above 12. The measurement against the open for all orders (+28.2 per side, +21.8 to +35.4) is higher by the buy drift, positive on both sessions here (+33.4, +1.3) and +30.8 over $200M, sign in general untested.

## Evidence summary
| Claim | Source | Tier | Key figure | OOS / post-pub? | Net of costs? |
|---|---|---|---|---|---|
| Per-side paper cost vs arrival, three sessions | Own queries on the store backup plus Alpaca SIP quotes | Own result | +24.2 bp (+18.9 to +30.6) | Paper only | n/a |
| Buys sent after the open fill between mid and ask, about 6 ms after submit | Own queries plus SIP quotes | Own result | 194 of 194; fill 14.8 bp under the ask | Paper only | n/a |
| Buys wait for every sell to finish, by design | `docs/specs/paper-trading.md` req 3; `execution/wrapper.py` (`_trade`, `_await_sells`, `_buys`, `_buys_cash`) | Code / spec (fact) | sell wait 900 s, poll 15 s; buys 375 to 618 s after the open | n/a | n/a |
| Buy drift between the open and the submit | Own queries | Own result | +16.4 bp (+2.4 to +30.5); sessions +33.4 and +1.3 | Paper only | n/a |

## Disconfirmation
- **Searches run:** none new. The [10-25 note](2026-10-25-paper-fill-costs.md) covered Alpaca's paper-fill and order-handling documents; this note adds no external claim.
- **What was checked against the conclusion:** (a) the rebalance days alone, without the larger 10-20 build orders: +17.0 bp, still above 12; (b) winsorizing, equal session weights and notional weights: all above 12; (c) the liquid band alone: +10.6 bp against arrival, under 12 with an interval that contains it, but +17.9 against the open as executed and +27.3 to +28.9 notional-weighted. So a pass needs both liquid names and buys sent at the open. (d) Whether the late buys are the broker's doing: no, each buy filled milliseconds after we sent it, and no sell was open when the buys went in.

## Caveats & gaps
- **Paper fills are simulated.** Alpaca's paper engine matches against the NBBO with random partial fills; it does not measure live slippage. The arrival figure for buys sent after the open (fill inside the quoted spread) looks like a plausible marketable-order cost, but live price improvement and opening-auction treatment are not measured.
- **Three sessions, one of them a build day.** Rung 1 asks for two weeks of fills. The orders of one session share one market move, so the order bootstrap understates the uncertainty. The 10-26 and 10-27 orders are $1 to $50; 10-20's are $494. Size, session and submit timing coincide, so the 10-20 versus later-days contrast cannot be split between them.
- **Arrival for pre-open orders is the SIP daily open,** not the primary's official opening price and not a midpoint, so a pre-open order's cost includes the open print's position in the spread.
- **The arrival quote is the last SIP quote at or before `submitted_at`,** median 2.3 s old. A stale or one-venue quote can misplace the mid.
- **The drift term** is measured on two sessions: positive on both (+33.4, +1.3) and +30.8 bp over $200M; its sign in general for momentum names in the first ten minutes is untested.
- 10-29's sells (82, halted run) are not included.

## UNVERIFIED items
- Whether B10's names fall mostly above $200M of daily dollar volume.
- Whether Alpaca's live opening-print protection applies to notional (fractional) orders (carried from the 10-25 note).
- How the 132 to 152 s between the last sell fill and the journaled collection splits between the poll loop and the collection itself (no timing log).

## Follow-up questions (not answered here)
- Should the buys phase poll and submit faster (concurrent submits, a shorter poll), or size buys before the open from expected proceeds? Both change execution code or the spec's cash rule; neither is in this note's scope.
- Rerun these figures when two weeks of rebalance sessions have filled, and on B10's own names once its paper book trades.

## Sources
- Store backup `/Users/josejuarez/Projects/tradepartner/data/tradepartner.duckdb.bak-20261029-1629` (read-only; taken 2026-10-29 16:29 UTC): `orders`, `order_events` (`raw_json.submitted_at`), `fills`, `decisions`, `prices_daily`, `paper_runs`, `paper_run_results`, `paper_windows`.
- Alpaca SIP historical quotes (the last 20 quotes in the 120 s before each post-open buy's `submitted_at`) and 09:30 to 09:46 ET minute bars for the order symbols, fetched through the repo's `adapters.alpaca_raw` data client; raw pulls and scripts (`fetch.py`, `analyze.py`, `extra.py`) in `/Users/josejuarez/tradepartner-probes/fillcost/`, outside the repo. Minute bars were pulled but not used in the figures.
- Code: `src/tradepartner/execution/wrapper.py` (`_trade`, `_await_sells`, `_buys`, `_buys_cash`), `execution/phases.py`, `config.py` (`PaperConfig`: `sell_wait_seconds` 900, `poll_interval_seconds` 15), `execution/slippage.py`.
- Spec: [paper-trading](../specs/paper-trading.md) req 3.
- Prior notes: [2026-10-25 paper fill costs](2026-10-25-paper-fill-costs.md), [2026-10-28 strategy frontier, fill audit](2026-10-28-strategy-frontier-verified.md#fill-audit-re-derived-3-sessions-593-orders-918-fills); kill-ladder rung 1 in the [hypothesis backlog](hypothesis-backlog.md) priority note.
