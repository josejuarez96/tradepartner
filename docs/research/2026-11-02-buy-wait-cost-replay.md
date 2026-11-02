# Research Report: Replay of what buys pay for waiting on the sells (T371)

**Brief:** #2884  ·  **Date:** 2026-11-02  ·  **Status:** COMPLETE for the six order sessions in the store copy (2026-10-20 to 11-02); four of them have post-open buys  ·  **Agent/model:** team opusreplay, claude-opus-5-5; read-only queries on a store copy taken 2026-11-02 after both runs, one small Alpaca SIP pull, a code read of `execution/wrapper.py` and `execution/run.py`

## Answer
**Verdict:** INSUFFICIENT (on paper): the replay cannot tell whether sending buys before the open saves money  ·  **Confidence:** low

1. **The cost of waiting (the drift from the open to the send) averages zero over four sessions, and it swings by more than 2% from one session to the next.** Daily-book buys sent after the open drifted **-0.3 bp** (order bootstrap -15.5 to +15.6, n 420). The four session means were +33.4, +1.3, +71.1 and -139.6 bp. Bootstrapping over the four sessions gives -96.7 to +56.9. On 10-26, 10-27 and 10-30 waiting cost money. On 11-02 the names fell after the open, so waiting paid.
2. **Under the pinned counterfactual, sending every post-open buy before the open saves +14.7 bp per buy** (order bootstrap -2.3 to +31.7). That is **+7.3 bp per side** and about **+95 bp a year** at the daily book's turnover (about 6.5 times equity bought per year). Over sessions the interval is -82 to +72 bp per buy. Almost all of the +14.7 is the spread, not the drift. A buy sent after the open pays +22.8 bp against the mid. A pre-open sell in the same liquidity band paid about +7.8 bp against the open, median 0. The saving therefore depends on one assumption: that Alpaca's simulator fills a pre-open market buy at the open print, as it fills our pre-open sells. If a pre-open buy pays the same half-spread, the sign flips to -21.7 bp.
3. **Under the amended cash rule, V1 could have sent few of these buys early.** The daily book holds almost no cash before its run. Before the open it held $8 to $153 on three of the four sessions (0.3% to 3% of that day's buys). Only after the halted 10-29 run did it hold $3,802 (67% of 10-30's buys). Scaling the +95 bp a year by the share of buys that cash covers gives about **+17 bp a year**. Almost all of that comes from the one session after a halt.
4. **The buys are late for the reasons the 10-30 note found, and the second book waits too.** Daily buys went out 375 to 688 s after the open. That delay is the slowest sell's simulated fill (164 to 305 s), then 11 s of polling, about 155 s of fill collection, about 72 s of sizing, then serial submits at 1.56 s each. The phase notes (#2621) split this out for the first time on 11-02. The main book had no sells, yet its buys went out 1,075 to 1,231 s after the open. `every_book_run` runs the books one after another, so main's run started one second after daily's ended.

## Evidence

### Data, definitions and what was pinned
- **Method pinned before computing:** `~/tradepartner-probes/fillcost2/METHOD.md`, outside the repo. Deviations are listed at its foot and repeated under Caveats. Scripts: `fetch.py`, `analyze.py`, `extra.py`. Outputs: `analyze.out`, `extra.out`.
- **Orders.** 1,169 orders and 1,724 non-superseded fills, every order filled:
  - book `daily`: 10-20 (202 pre-open buys, $494 median, the build day), 10-26, 10-27, 10-29 (82 sells, run halted before any buy), 10-30 and 11-02;
  - book `main`: 11-02 (101 buys, $989 median, its first orders, no sells).
  - Median daily-book order size after the build: $6 to $23.
- **Definitions** are those of the [10-30 note](2026-10-30-paper-fill-cost-arrival.md):
  - order price is the fill VWAP;
  - arrival is the open for a pre-open order and the SIP mid at `submitted_at` for a post-open order (quote age median 1.7 s);
  - cost vs arrival = s(VWAP/arrival - 1), cost vs open = s(VWAP/open - 1), drift = s(arrival/open - 1), with s = +1 for a buy and -1 for a sell, in bp;
  - liquidity band: 20-session median dollar volume, split at $50M and $200M;
  - intervals: an iid bootstrap over orders, 10,000 resamples, seed 2842.
- **Open.** The session's SIP daily-bar open from the store. The store has no 11-02 bar, so that day's open comes from the same Alpaca SIP daily bar. Pulled the same way for 10-30, it matched the store on all 196 symbols. The open is at 09:30 New York time, which is 14:30 UTC on 11-02 (EST).
- **Reproduction:** on the 10-30 note's three sessions this code gives buys +38.9 / +46.9 / +8.0 bp (vs arrival, vs open, drift) and sells +9.5. Those are the note's figures.
- **Counterfactual.** For each post-open buy: saving = actual cost vs open minus a reference cost vs open. The reference is the mean of the pre-open orders in the buy's liquidity band. Positive means "sending it before the open would have been cheaper".
  - R0, the primary reference, is the daily book's pre-open sells (444, 10-26 to 11-02). They are the same size as the daily post-open buys and are filled the same way at the open.
  - R1 to R4 are sensitivities. The saving's bootstrap resamples the buys and the reference orders together.

### Q1: when the buys went out, and why

| Session, book | Last sell filled | Sell fills journaled | Buys journaled | Buys sent (s after open) | Spacing |
|---|---|---|---|---|---|
| 10-26 daily (91 buys) | +253 | +405 | +478 | +479 to +618 | 1.55 s |
| 10-27 daily (103) | +164 | +296 | +374 | +375 to +532 | 1.55 s |
| 10-30 daily (135) | +273 | +344 | +457 | +458 to +667 | 1.56 s |
| 11-02 daily (93) | +305 | +446 | +543 | +544 to +688 | 1.56 s |
| 11-02 main (101) | no sells | n/a | +1074 | +1075 to +1231 | 1.57 s |

- **The 11-02 phase notes** (`paper_run_results.message`, #2621) are the first to split the gap:
  - the last sell filled at +305 s;
  - the poll saw the sells terminal at +316 s (84 poll rounds);
  - the fill collection ran from +316 to +471 s (**155 s**);
  - the buys were sized at +543 s (72 s later);
  - the first buy went out at +544 s and the last at +688 s.

  In the 10-30 note's two sessions, the 132 to 152 s between the last fill and the journaled collection was therefore mostly the collection itself, not the poll.
- No sell was still unfilled when the first buy went out, on any session. On 10-30 and 11-02 every buy row has `sells_in_flight_at_submit` true. The flag is `not last` on a buy (`wrapper.py` step 7), a batch-scope flag, so it is not evidence of a sell in flight.
- Each daily buy filled a median 6 to 7 ms after it was sent. Main's larger buys took a median 0.84 s, and 95% of them were split into several fills.
- **Main's delay is not the sell wait.** `run.py::every_book_run` runs the open books in order, so main (run 46) started at 09:44:43, one second after daily's run 45 ended. A pre-open tranche on main would need main's run to start before the open.

### Q2: the drift (open to send) of every post-open buy

| Cut | n | Drift, bp (95%, orders) | vs arrival | vs open |
|---|---|---|---|---|
| Daily 10-26 | 91 | +33.4 (+12.9 to +55.2) | +21.9 | +55.3 |
| Daily 10-27 | 103 | +1.3 (-17.3 to +19.0) | +26.7 | +28.1 |
| Daily 10-30 | 134 | +71.1 (+40.9 to +103.1) | +23.0 | +94.2 |
| Daily 11-02 | 92 | -139.6 (-172.4 to -107.3) | +19.0 | -120.8 |
| **Daily, pooled** | 420 | **-0.3 (-15.5 to +15.6)** | +22.8 (+20.2 to +25.6) | +22.5 (+6.8 to +38.1) |
| Main 11-02 | 100 | -133.6 (-168.8 to -98.8) | +24.9 | -109.0 |
| Daily and main pooled | 520 | -26.0 (-41.2 to -10.9) | | |
| Daily, under $50M | 22 | +10.8 (-63.6 to +77.0) | +71.8 | +82.6 |
| Daily, $50-200M | 191 | +7.4 (-11.9 to +28.0) | +27.8 | +35.2 |
| Daily, over $200M | 207 | -8.7 (-33.3 to +16.1) | +13.0 | +4.3 |

- **Session-cluster view.** The orders of one session share one market move.
  - Daily session means: +33.4, +1.3, +71.1, -139.6 bp; their mean is -8.4 and their SD 92.0.
  - A t-interval over the four means gives -155 to +138.
  - A bootstrap over sessions gives the pooled -0.3 with an interval of -96.7 to +56.9. With four sessions it can only produce 35 distinct resamples.
  - Adding main 11-02 as a fifth cluster gives -26.0 (-107 to +46). Main moved with daily that morning, so it is not an independent fifth session.
  - **Four sessions cannot sign the drift.** The 10-30 note's "positive on both days" no longer holds.
- **Notional-weighted drift:** daily -18.1, main -133.6.
- **Where the drift accrues** (mean of s × (minute close/open - 1) over the buys, from the 09:30 to 10:00 SIP minute bars):

  | Session | End of minute 1 | Minute 5 | Minute 10 | Minute 15 |
  |---|---|---|---|---|
  | 10-26 | -16 | -7 | +49 | +46 |
  | 10-27 | -3 | -14 | +19 | +19 |
  | 10-30 | +3 | +21 | +75 | +38 |
  | 11-02 daily | -47 | -93 | -133 | -159 |

  On 10-26 and 10-30 the largest move came between minutes 5 and 10, when the buys go out. On 11-02 the fall ran from the first minute to minute 15.

### Q3: the counterfactual and how much it depends on its assumption

**Is the 10-20 pre-open buys' +52.7 bp a build-day artefact?** Yes, and it is set by order size, not by the liquidity-band mix:
- **Band mix is the same.** The 10-20 buys were 6% / 49% / 46% across the bands (under $50M, $50-200M, over $200M), against 5% / 45% / 49% for the daily post-open buys. Reweighted to the post-open mix they still cost +50.1 bp against the open. The R0 sells, reweighted the same way, cost +7.8.
- **Fill splitting drives the cost.** 76% of the 10-20 buys ($494 each) were split into several fills, against 6% of the pre-open sells ($14). One-piece 10-20 buys cost -2.3 bp (n 48), in line with one-piece pre-open sells (+1.1, n 416). Split buys cost +69.8, split sells +127.9.
- **Cost rises with order size relative to the name's dollar volume.** By quartile of that ratio, the 10-20 buys cost -6, +48, +70 and +99 bp, and the split share rose from 43% to 90%. 12 of their VWAPs sit above the day's SIP high.
- **After the open, size does not add cost.** Main's $989 buys on 11-02 paid +24.9 bp against arrival, close to the daily book's $13 buys (+22.8). The split-fill premium shows up only at the opening print.

So the 10-20 buys measure what the simulator does to $500 orders at the open. They are kept below as sensitivity R1, not as the reference for the daily book's $6 to $23 buys.

Saving per daily post-open buy (actual vs open +22.5 bp, n 420):

| Reference (what a pre-open buy would have cost vs the open) | Saving per buy, bp (95%) | Per side | Per year, bp of equity |
|---|---|---|---|
| **R0: pre-open sells, same band (pinned)** | **+14.7 (-2.3 to +31.7)** | **+7.3 (-1.1 to +15.8)** | **+95 (-15 to +205)** |
| R0, interval over sessions | +14.7 (-82.5 to +72.2) | +7.3 (-41 to +36) | +95 (-534 to +468) |
| R1: 10-20 pre-open buys, same band | -27.6 (-47.6 to -7.7) | -13.8 | -179 |
| R2: the buy's own cost vs arrival (spread paid anyway; only the drift is saved) | -0.3 (-16.1 to +15.2) | -0.1 | -2 |
| R3: fills exactly at the open | +22.5 (+6.3 to +38.2) | +11.2 | +146 |
| R4: R0 plus the band's post-open half-spread | -21.7 (-38.5 to -4.5) | -10.8 | -141 |

- **R0 by session:** +48.3, +17.0, +86.4 and -125.6 bp. Its mean of session means is +6.5 (t-interval -141 to +154).
- **R0 by band:** under $50M -1.5 (-114 to +99); $50-200M +30.0 (+8.6 to +52.1); over $200M +2.3 (-23.2 to +27.6).
- **R0 notional-weighted:** +8.6.
- **The R0 reference itself:**
  - by band: +84.2 (n 30, median 0, a few outliers), +5.3 and +2.0 bp;
  - by session: +0.5, +20.0, -3.6, +28.5 and +7.0. Pre-open sells are not free either.
- **Main 11-02** (not a sell-wait case): R0 -115.5 (-151.5 to -78.5). On that falling morning, an earlier send would have cost more.

### Q4: bottom line
- **Per buy:** +14.7 bp (-2.3 to +31.7), R0, orders independent.
- **Per side:** +7.3 bp (-1.1 to +15.8). Sells are already sent pre-open, so they save nothing.
- **Over sessions** the interval is -82 to +72 per buy. It contains every sensitivity above.
- **Per year:** the daily book bought 15.4% of equity over the six sessions from 10-26 to 11-02 (4.72%, 2.36%, 5.56% and 2.79% on the four sessions with buys; 10-26 and 10-30 include catch-up after a skipped or halted run). That is 2.57% per elapsed session, or 6.5 times equity a year over 252 sessions. The R0 saving is therefore +95 bp a year (-15 to +205, order bootstrap). Counting from 10-21, the first session after the build, gives 4.3 times and +63 bp.
- **Under V1's cash rule** (cash already held, no sell proceeds), only the cash-funded part could go early: 17.7% of buys on average (25.7% weighted by notional), and nearly all of it on the one post-halt session. That gives about +17 bp a year (+24 weighted by notional).
- **The term that matters is the spread, not the drift.** The replay measures the drift well enough to call it zero on average and large on any one day. Whether a pre-open buy avoids the half-spread depends on the simulator, and only V1 itself can show it.

## Evidence summary
| Claim | Source | Tier | Key figure | OOS / post-pub? | Net of costs? |
|---|---|---|---|---|---|
| Drift of daily post-open buys, open to send | Own queries on the store copy plus SIP quotes | Own result | -0.3 bp (-15.5 to +15.6); session means +33.4, +1.3, +71.1, -139.6 | Paper only | n/a |
| Saving from sending buys pre-open, R0 counterfactual | Own replay | Own result (model-dependent) | +14.7 bp per buy (-2.3 to +31.7); sessions -82.5 to +72.2 | Paper only | n/a |
| 10-20 pre-open buy cost is a size and split-fill artefact | Own queries | Own result | one-piece -2.3 bp vs split +69.8; quartiles of size per dollar volume -6 to +99 | Paper only | n/a |
| Buy delay = slowest sell + poll + 155 s collection + 72 s sizing + serial submits | `paper_run_results` phase notes (#2621), `orders`, `fills` | Code / journal (fact) | 11-02: +305 → +316 → +471 → +543 → +544 to +688 s | n/a | n/a |
| Second book waits for the first | `execution/run.py::every_book_run`; `paper_runs` 45/46 | Code / journal (fact) | main buys +1,075 to +1,231 s | n/a | n/a |
| V1 can fund few daily buys pre-open | `positions_daily` cash; PR #2881 cash rule | Own result | 0.3% to 3% of buys (67% after the halted 10-29) | n/a | n/a |

## Disconfirmation
- **Searches run:** none new. This is a replay of our own journal, and no external claim is made.
- **What was found against:**
  - The 10-30 note's positive drift (+16.4 bp) did not survive two more sessions. 11-02 alone is -140.
  - The R0 saving relies on the half-spread being avoided. If a pre-open buy pays it (R4), or if the reference is the 10-20 buys (R1), the sign flips.
  - The saving's session interval runs from -82 to +72 bp per buy.
  - Under V1's actual cash rule, the daily book can send almost nothing early on an ordinary day.

## Caveats & gaps
- **Paper fills come from Alpaca's simulator.** Pre-open market orders fill at the open print here, usually in one piece when small. Live opening-auction behaviour, and the half-spread a live pre-open buy pays, are not measured.
- **Few sessions.** There are four sessions with post-open buys, and one of them (11-02) carries most of the variance. Order-level intervals understate the uncertainty, so read the session view.
- **The counterfactual is a model, not a measurement.** It borrows the cost of pre-open *sells* for buys and assumes the simulator treats them alike. It also ignores that an earlier buy changes nothing else in the run.
- **This does not replace** the spec's alternating V0/V1 measurement (T373) or T371's sandbox run before the open (#2881).
- **Deviations from the pinned method** (`METHOD.md`):
  - (1) three post-open buys had no valid SIP quote in the 120 s window and are dropped (RUSHB 10-30, SNEX 11-02, CHEF main 11-02);
  - (2) the minute-bar drift path, the notional-weighted lines, the five-cluster bootstrap and the t-intervals over session means were added as description;
  - (3) the V1 cash-eligible share was added after reading PR #2881;
  - (4) after the quant-auditor's review, the annual turnover spreads the buys over the six elapsed sessions (6.5 times a year), not over the four sessions with buys as pinned (9.7 times), which counted catch-up twice.

  None of these changes the per-buy R0 figure.
- **Annualising** spreads the measured buys over the elapsed sessions (six from 10-26). The daily book traded buys on 4 of those 6 sessions (10-28 kill switch, 10-29 halted). The sessions after a skip or a halt caught up. A per-trading-session average would count that catch-up twice (9.7 times, +143 bp a year).

## UNVERIFIED items
- Whether Alpaca's simulator fills a pre-open notional market *buy* at the open print as it does a sell. The only pre-open buys are 10-20's large orders, and only their one-piece fills (n 48, -2.3 bp) suggest it does.
- Whether a live opening auction charges a small pre-open buy any spread.

## Follow-up questions (not answered here)
- Should the open books run before the open in parallel, or main first? Today `every_book_run` delays the second book's buys by the whole first run, about 15 minutes on 11-02.
- Would a shorter fill collection (155 s on 11-02), faster sizing (72 s) or concurrent buy submits (2.3 to 3.5 minutes per batch) matter? Together they are 3.5 to 4 of the 6 to 11 minutes the buys wait, but the drift they would avoid has no measured sign.

## Sources
- Store copy `store-20261102.duckdb` (read-only; taken 2026-11-02 after both runs): `orders` (`book_id`, `sells_in_flight_at_submit`), `order_events` (`raw_json.submitted_at`), `fills`, `prices_daily`, `paper_runs`, `paper_run_results` (phase notes), `positions_daily`, `paper_windows`.
- Alpaca SIP data through the repo's `adapters.alpaca_raw` data client:
  - quotes in the 120 s before each post-open buy's `submitted_at` (329 new, 194 reused from the 10-30 note);
  - 09:30 to 10:00 ET minute bars for the 10-29, 10-30 and 11-02 order symbols;
  - daily bars for 10-30 (check) and 11-02.

  Scripts and raw pulls are in `/Users/josejuarez/tradepartner-probes/fillcost2/`, outside the repo.
- Code: `src/tradepartner/execution/wrapper.py` (`_trade`, `_await_sells`, `_buys_cash`, step 7's flag), `src/tradepartner/execution/run.py` (`every_book_run`).
- Spec and PRs: [paper-trading](../specs/paper-trading.md) req 3 and amendment 2026-11-01 (#2842); T371 PR #2881.
- Prior note: [2026-10-30 paper fill cost against arrival](2026-10-30-paper-fill-cost-arrival.md).
