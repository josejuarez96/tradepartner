# Research Report: Alpaca fractional orders at the open; probes for corporate-actions depth and missing-bar share

**Brief:** issue #86 ([brief comment](https://github.com/josejuarez96/tradepartner/issues/86#issuecomment-5828419552))  ·  **Date:** 2026-09-25  ·  **Status:** COMPLETE. Every sub-question has a Tier 1 or Tier 2 answer, or is marked "not documented" with a search log. Probe protocols for all three parts of #86 are in the last section. One timing conflict between two Tier 1 Alpaca documents is still open (see Caveats).  ·  **Agent/model:** research agent (team otarix), claude-opus-5-5

This report builds on [2026-09-25-free-data-terms.md](2026-09-25-free-data-terms.md). References of the form "FDT S6" point to that report's source list. New sources here are numbered S21 and up, so the two lists do not collide.

## Answer
**Verdict:** n/a. This is a factual brief, not a hypothesis, so the grades do not apply. Coverage: the fractional path is answered from Tier 1. The whole-share path is **not documented** at the point that matters (auction or not), and the Tier 2 staff answers on it conflict. The paper-vs-live difference is answered from Tier 1 plus Tier 2.  ·  **Confidence:** medium for the fractional answer and for paper vs live. Low for the whole-share answer.

**Fractional (for example a $5 notional market DAY order submitted at 09:00 ET).** Alpaca fills NMS fractional orders "entirely out of Your inventory and without purchasing or selling shares in the market" and prices them "between the National Best Bid and Offer ("NBBO") at the time of the order" (S22, §28). The fractional portion is "always filled/executed directly by Alpaca" (S24, staff). So a pure fractional order does not reach an exchange, and therefore cannot take part in the primary-exchange opening auction. It is filled by Alpaca as principal against the NBBO once regular hours begin. OPG is not available for fractional orders (FDT S7). No source says exactly *which* NBBO is used for an order queued before the open: the quote when it was submitted (a pre-market quote) or the quote when it was released after 09:30. The docs literally say "at the time the order was submitted" (FDT S6), which, read literally, would mean the pre-market quote. **Not documented; the probe settles it.**

**Whole share (1 share, market, DAY, submitted at 09:00 ET).** The order is "queued and routed to a market maker the following morning prior to the regular trading open" and is then "eligible for execution during regular trading hours" (S22, §26). In 2026 Q2, 100% of Alpaca's non-directed equity orders went to four wholesalers: Virtu, Citadel, Jane Street and GTS (S23). No exchange is listed. Alpaca documents only one thing about auction participation: OPG orders execute "only in the market opening auction" (S25b; FDT S7). Whether a wholesaler fills a queued DAY market order in the primary auction or against the NBBO just after the open is **not documented**. Alpaca staff have said both: "The price most often will be the opening auction price" and "The only thing driving the order fill price is the NBBO quote at the time the order filled (typically 50ms-500ms after open)" (S25, Tier 2).

**Paper vs live.** Paper fills match "against the best available current market price (NBBO)". Paper does not simulate "Price improvement received" or "Price slippage due to latency" (S27). Staff state that "OPG orders in paper trading are simulated as regular market orders" (S26, Tier 2). So paper cannot reproduce an auction fill for any order type. Paper results measure paper's own simulator, not live execution. **No source gives how far any of these fills sit from the official open or from Alpaca's first-trade bar open.** Only the probe (paper) and a small live test (an owner decision) can measure that.

## Evidence
All pages were seen on 2026-09-25.

| # | Claim | Source | Tier | Key figure (quoted) | OOS / post-pub? | Net of costs? |
|---|---|---|---|---|---|---|
| 1a | Fractional NMS orders are filled from Alpaca's inventory, not in the market | S22 Customer Agreement V26.2026.07, §28 "Fractional Trades", p.15 | 1 | "You fulfill my fractional order for national exchange-listed securities ("NMS Securities") entirely out of Your inventory and without purchasing or selling shares in the market ("Inventory Fulfillment")" | n/a | n/a |
| 1b | Fractional pricing reference | S22 §28, p.15 | 1 | "you will endeavor to price such shares or fractional shares at a price between the National Best Bid and Offer ("NBBO") at the time of the order for orders made during market hours" | n/a | n/a |
| 1c | For a mixed order, the fraction copies the whole-share price | S22 §28, p.15; S21 | 1 | "the fractional component of that order will be fulfilled at the execution price received for the corresponding whole shares"; S21: "If you submit an order for a whole and fraction, the price for the whole share fill will be used to price the fractional portion." | n/a | n/a |
| 1d | Principal capacity | S21 Fractional Trading doc | 1 | "Fractional share transactions are executed either on a principal or riskless principal basis, and can only be bought or sold with market orders during normal market hours." | n/a | n/a |
| 1e | Staff: fraction filled by Alpaca, whole portion routed | S24 forum, Dan_Whitnable_Alpaca (staff), 2025-03-24 | 2 | "The fractional portion of orders are always filled/executed directly by Alpaca. The whole share portion (if any) is routed to an execution partner." "Alpaca matches that fill price for any fractional portion." | n/a | n/a |
| 1f | Fractional orders queued for the open | S22 §28, p.16 | 1 | "Certain securities are not eligible for fractional trading during extended hours. During extended hours, orders in such securities may be placed for whole shares or queued for the opening of market hours." | n/a | n/a |
| 1g | Fractional TIF limits; OPG and CLS not available | FDT S6, S7; S25b (re-fetched) | 1 | Fractional orders are "day" only. GTC, IOC, FOK, OPG and CLS are not permitted | n/a | n/a |
| 2a | Queued orders are routed to a market maker before the open | S22 §26, p.14 | 1 | "orders submitted after regular trading hours that have not been elected to be eligible for execution during the extended hours trading session will be queued and routed to a market maker the following morning prior to the regular trading open. The order will then be eligible for execution during regular trading hours." | n/a | n/a |
| 2b | **Timing conflicts with 2a:** an older disclosure says queued orders go out *after* the open | S29 Use and Risk Disclosures v.2.2020.11, p.1 | 1 | Queued Orders "are sent out for execution the next day of trading shortly after the market opens. Each Queued Order request is sent per customer and per security as Alpaca market orders, and they are not aggregated." | n/a | n/a |
| 2c | Routing destinations: wholesalers only | S23 Rule 606(a)(1) report, 2026 Q2, April/May/June tables | 1 | S&P 500, April 2026: Citadel 35.29%, Jane Street 33.01%, Virtu 27.03%, GTS 4.66% of non-directed orders. Non-directed = "100.00" % of all orders. No exchange venue is listed in any month | n/a | n/a |
| 2d | Routed orders can be filled in primary auctions (at least some) | S23, Material Aspects (Virtu, Jane Street, GTS) | 1 | "Orders filled on the primary market opening and closing auctions/crosses will not receive a rebate nor incur a charge." Citadel: "Orders filled on the primary auction will not receive a rebate or charge." This does not say which order types (OPG vs DAY) end up in the auction | n/a | n/a |
| 2e | Only OPG is documented as auction-only | S25b Placing Orders doc | 1 | OPG "is eligible to execute only in the market opening auction"; "OPG orders submitted after 9:28am but before 7:00pm ET will be rejected" | n/a | n/a |
| 2f | Staff on pre-open DAY market orders (part 1): opening price "most often" | S25 forum thread 13787, Dan_Whitnable_Alpaca, 2024-03-05 | 2 | "market orders placed before markets open, are executed at market open at the 'open' price." "The price most often will be the opening auction price." "If one specifies a time in force of either `opg` or `cls` … then your order will participate in these auctions." | n/a | n/a |
| 2g | Staff on the same (part 2): NBBO after the open | S25, same author, 2024-03-06 | 2 | "Buy orders typically fill at the NBBO ask price and sell orders typically fill at the bid price." "The only thing driving the order fill price is the NBBO quote at the time the order filled (typically 50ms-500ms after open)." | n/a | n/a |
| 2h | OPG fills at the primary-exchange auction price, not the first-trade open | S26 forum thread 14672, Dan_Whitnable_Alpaca, 2024-07-20/21 | 2 | "An Alpaca OPG order will fill at the opening auction price on the primary exchange." Example: UBER NYSE auction 66.56 (614,454 shares) vs an earlier Nasdaq print at 66.78 (359 shares) | n/a | n/a |
| 3a | Paper matches against the NBBO | S27 Paper Trading doc; FDT S8 | 1 | "all orders submitted in paper trading will be matched against the best available current market price (NBBO)" | n/a | n/a |
| 3b | What paper does not simulate | S27 | 1 | "Market impact of your orders"; "Information leakage of your orders"; "Price slippage due to latency"; "Order queue position (for non-marketable limit orders)"; "Price improvement received"; "Regulatory fees"; "Dividends" | n/a | n/a |
| 3c | Paper OPG is simulated as a market order | S26, 2024-07-21; S25, 2024-03-07 | 2 | "OPG orders in paper trading are simulated as regular market orders"; "one area which paper trading doesn't simulate well is `opg` and `cls` orders" | n/a | n/a |
| 3d | Fractional is available in both paper and live | FDT S6 | 1 | "all Alpaca accounts are allowed to trade fractional shares in both live and paper environments" | n/a | n/a |
| 4 | Non-extended orders outside hours are queued for the next session | S28 24/5 Trading doc | 1 | "Orders not designated for extended hours execution will be queued for the next trading session." "Only `limit` orders are supported during the overnight session." | n/a | n/a |

## Disconfirmation
- **Searches run** (8 web searches, 14 page or PDF fetches):
  1. "Alpaca Securities Rule 606 report order routing". Led to S23.
  2. "Alpaca fractional order submitted before market open fill opening price forum". Led to S24.
  3. "forum.alpaca.markets market order submitted before open filled opening auction price 9:30". Led to S25 and S26, plus thread 11664 (fetch failed with "socket hang up", not read).
  4. "Alpaca notional fractional order queued overnight executed at open price Dan Whitnable". Nothing new beyond S28.
  5. "FINRA fractional share orders cannot be routed to exchanges broker-dealer principal execution regulatory notice". The FINRA 2023 exam-findings page on fractional shares was fetched. It covers reporting only and has nothing on execution timing.
  6. to 8. Tier 1 corporate-action ground truth for the probe (ISRG, MNST, NVDA, GE).
  - Fetches: Alpaca Placing Orders, Fractional Trading, Paper Trading (twice), 24/5 Trading, the Disclosures index, the 606 2026 Q2 PDF, the Use and Risk PDF, the Customer Agreement PDF, forum threads 16562, 13787 (twice) and 14672.
- **Evidence that pre-open fractional DAY orders fill in the auction or at the official open:** none found. Every Tier 1 statement points the other way: inventory fulfilment "without purchasing or selling shares in the market" (S22 §28), principal capacity (S21), and no OPG for fractional (FDT S7). One edge case stays open. For an order with a whole-share part, the fraction copies the whole-share fill price (S22 §28, S24). If a wholesaler ever fills that whole-share part in the auction, the fraction would get the auction price too. That makes the whole-share question (below) relevant even to fractional orders larger than one share.
- **Evidence that pre-open whole-share DAY orders fill in the auction or at the official open:** partial, Tier 2 only, and self-contradicted. The same staff member wrote "executed at market open at the 'open' price" and "The price most often will be the opening auction price" (S25, 2024-03-05). A day later he wrote that the fill is driven by "the NBBO quote at the time the order filled (typically 50ms-500ms after open)" (S25, 2024-03-06). The 606 report confirms that some routed orders are filled in primary auctions (S23), but it does not say which order types. The Customer Agreement routes queued orders to the market maker "prior to the regular trading open" (S22 §26), which would let a wholesaler enter them into the auction. That is possible, not documented. **Not documented; the probe (paper) cannot settle it, because paper has no auction. Only a live test can.**
- **Evidence against "paper approximates live at the open":** found (S27 list; S26 and S25 staff answers). Paper omits price improvement and latency slippage, and simulates OPG as a market order.

## Caveats & gaps
- **Timing conflict (Tier 1 vs Tier 1).** The Customer Agreement (V26.2026.07) says queued orders are routed "prior to the regular trading open" (S22 §26). The Use and Risk Disclosures (v.2.2020.11) say they are sent "shortly after the market opens" (S29). The Customer Agreement is newer and incorporates the Use and Risk Disclosures by reference, so it is probably the current practice. Treat it as **probable, not certain**.
- **Which NBBO prices a queued fractional order is not documented.** S22 says "at the time of the order for orders made during market hours". A 09:00 submission is not "during market hours". FDT S6 says "at the time the order was submitted". Neither covers a queued pre-open order unambiguously.
- S21's sentence "can only be bought or sold with market orders during normal market hours" is out of date next to the same page's statement that limit and extended-hours fractional orders are supported. The principal-capacity part is consistent with S22 and S24.
- The Rule 606 report covers orders Alpaca *routes*. Fractional fills from Alpaca's inventory are not routed, so they may not appear in it. The report gives no timing or auction breakdown.
- Tier 2 staff statements (S24 to S26) come from one person on a community forum. They are useful, but they are not policy.
- The S29 PDF is dated v.2.2020.11. It is still linked from the disclosures index, so it has not been withdrawn, but it may be stale.

## UNVERIFIED items
- Whether wholesalers enter queued whole-share DAY market orders into the primary opening auction. **Not documented** (live test only).
- Which NBBO snapshot prices a queued fractional order: the submission-time pre-market quote, or the quote at release after the open.
- The typical gap, in basis points, between Alpaca fills and (a) the official open or (b) the SIP first-trade bar open. No source quantifies either. The only example is FDT S20: XOM bar open 118.54 vs auction 118.89, and that is bar vs auction, not a fill.
- GE's 1-for-8 reverse split effective 2021-08-02 comes from a search summary. It was not read in a GE 8-K. It is marked "verify" in the probe table. Probe 1 found Alpaca carries the same date and ratio; that is agreement between two secondary sources, still not the 8-K.
- The SEC `company_tickers_exchange.json` path suggested in Probe 2 was not fetched this session.
- ~~The date field that Alpaca's corporate-actions `start`/`end` filter applies to is not documented.~~ Resolved by Probe 1: it is `process_date` (see "Probe 1 results").

## Follow-up questions (not answered here)
- Does Alpaca Elite or Smart Routing change routing for DAY market orders at the open? S24 mentions Elite's "Smart Routing rules" but does not describe them.
- Can a Rule 606(b)(1) customer request (S23 index page: "specific order routing and execution information for the preceding six months") reveal whether the owner's own live pre-open orders were auction-filled? It could answer the whole-share question for the owner's actual orders after a live test.
- Alpaca's delisted-symbol bar coverage on SIP (FDT follow-up, still open).

## Owner probe protocols (all three parts of #86)

All three probes are **owner-run with keys**. They use the free plan's SIP history, so every bar or trade query must have `end` at least 15 minutes in the past (FDT S1, S20). Run from the repo with `uv run python`. Save raw outputs outside the repo, or under the test-fixture conventions if you want to keep them. Nothing below writes to the store.

### Probe 1: corporate-actions depth (feeds the adjustment backfill horizon, ADR 0003)

**Call** (existing function, one call per calendar year, 10 calls in total):
```python
from datetime import date
from tradepartner.adapters import alpaca_raw

SYMS = ["AAPL", "KO", "MNST", "ISRG", "NVDA", "TSLA", "GE"]
out = {
    y: alpaca_raw.corporate_actions(SYMS, date(y, 1, 1), date(y, 12, 31)) for y in range(2016, 2026)
}
```
If a year errors with a range or limit message, split it into quarters and record the error text verbatim. The wrapper already passes `limit=None`, so the 1,000-row cap does not apply.

**Known events (ground truth)**, with the source for each:

| Symbol | Year | Event | Source |
|---|---|---|---|
| MNST | 2016 | 3-for-1, split-adjusted trading from 2016-11-10 | MNST press release 2016-10-14 and 8-K (SEC EDGAR 0000865752), search summary |
| ISRG | 2017 | 3-for-1, split-adjusted trading from 2017-10-06 | ISRG 8-K ex-99.1 2017-08-11 (SEC EDGAR 0001035267), search summary |
| AAPL | 2020 | 4-for-1, 2020-08-31 | Already confirmed by the owner's T3 recording (FDT, owner recording table) |
| NVDA | 2021 | 4-for-1, split-adjusted trading from 2021-07-20 | NVDA 8-K 2021-05 (SEC EDGAR 1045810) |
| GE | 2021 | 1-for-8 reverse split, 2021-08-02 | Search summary only, **verify** in GE's 8-K |
| GE | 2023 | GE HealthCare spin-off, 1 GEHC per 3 GE, record date 2022-12-16, GEHC trading from 2023-01-04 | GEHC 10-Q 2023 (SEC EDGAR 1932393) |
| NVDA | 2024 | 10-for-1, split-adjusted trading from 2024-06-10 | NVDA 8-K 2024-05-22 (SEC EDGAR 1045810) |
| AAPL, KO | every year 2016 to 2025 | Quarterly cash dividends (about 4 per year each) | Company dividend histories. Exact dates were not pulled here; take them from each 10-K when grading |
| TSLA | 2016 to 2025 | Splits in 2020 and 2022 (from memory, **verify**); never a cash dividend | A negative control for dividends |

**Record, per year:** the number of events by type (`forward_splits`, `reverse_splits`, `cash_dividends`, `spin_offs` and so on); whether each ground-truth event is present with the right ratio or amount and the right ex-date; which date fields the payload carries (ex, record, payable, process) and which one the `start`/`end` window appears to filter on; any spurious events (for example a TSLA cash dividend).

**Decision this changes:**
- All events are present back to 2016. The adjustment backfill horizon is 2016, the same as bars (FDT S2, S17), and ADR 0003's assumption stands.
- The earliest complete year is Y, later than 2016. Adjusted history before Y has no Alpaca actions. Record Y in the backfill notes. Then the owner decides: cap `--since` at Y for adjusted series, or source pre-Y actions elsewhere (EDGAR 8-Ks). That is an ADR 0003 amendment, not a config key; no config key for the horizon exists today.
- Events are present but with wrong ratios or dates in some years. Treat those years as unreliable and handle them the same way as the "earliest complete year is Y" case.

### Probe 2: missing-bar share (feeds `ingest.max_missing_share`, currently 0.05)

**Symbol list, built without look-ahead as far as possible.** `alpaca_raw.assets_snapshot(symbols)` fetches one symbol per call. It cannot list the universe, and `get_all_assets` is not wrapped. Pick one of these:
- (a) **Preferred, no new code.** Take today's SEC ticker list (`company_tickers_exchange.json`, UNVERIFIED path), keep the NYSE, Nasdaq and NYSE American rows, and pass them through `assets_snapshot` in batches (about 6,000 to 8,000 calls, roughly 30 to 40 minutes at 200 per minute). Keep rows with `status == "active"`, `class == "us_equity"` and `tradable == True`.
- (b) A one-off call to `TradingClient.get_all_assets(GetAssetsRequest(status=ACTIVE, asset_class=US_EQUITY))` in a `spike/` scratch script. Do not add it to `alpaca_raw` without a plan task.

**Survivorship limits.** Both lists are as of *today*, which is after the window. Names delisted during the window are missing from the list. That matches the spec's denominator of "listed names" on later days, but it undercounts halts that ended in a delisting. Names listed during the window (IPOs) would show up as falsely missing before their first bar. Handle that with the rules below.

**Window.** Use the 20 most recent completed XNYS sessions ending at least 5 sessions before today, from `calendar.py`, never "weekdays". Call `alpaca_raw.daily_bars(batch, start, end, feed=DataFeed.SIP)` with batches of about 200 symbols. Record the returned `feed` echo.

**Formula.** For each session *s*:
- E_s = listed symbols that are *expected* on *s*. Exclude a symbol on sessions before its first bar in the window if that first bar is after the window start (probable IPO), and count those cases separately. Symbols with zero bars in the whole window go to a separate "never traded" count and are not in E.
- M_s = symbols in E_s with no bar on *s*.
- share_s = |M_s| / |E_s|.
- Report median(share_s), max(share_s), the session with the max, and the 10 symbols most often missing, with their asset `status` and `tradable`.

**Also measure the live case once.** On one normal day, run the same count for *that* session at the time ingest would run: the session close plus `ingest.settle_delay_minutes` (60). Historical sessions look more complete than the latest one does at ingest time.

**Tightening rule** (arithmetic for the owner; the owner sets the value):
- Candidate = max(2 × max(share_s), live-case share + 0.005), rounded up to the next 0.005.
- Never set it below the observed max or the live-case share. Doing so would flag normal days as stale, and the spec forbids silent relaxation later.
- If candidate < 0.05, tighten `ingest.max_missing_share` to the candidate. If the candidate is at least 0.05, keep 0.05 and investigate the top missing symbols first (they may be non-trading share classes that the universe filter should drop).

### Probe 3: paper-trading open probe (feeds `execution.fill_price` and the Phase 4 execution ADR)

**Orders** (paper account only, placed with alpaca-py `TradingClient(paper=True)` or the paper dashboard; `alpaca_raw` has no order function, and writing one is out of scope):
- Symbols: **KO** (NYSE-listed) and **AAPL** (Nasdaq-listed). Both trade well above $5, so a $5 notional order is purely fractional.
- Order F (fractional): `MarketOrderRequest(symbol=S, notional=5, side=BUY, time_in_force=DAY)`, with `extended_hours` left false.
- Order W (whole share): `MarketOrderRequest(symbol=S, qty=1, side=BUY, time_in_force=DAY)`.
- Optional Order O (control): `qty=1, time_in_force=OPG`, submitted before 09:28 ET. Paper simulates OPG as a market order (S26), so this checks whether paper treats OPG any differently.
- Submit all orders between 09:00 and 09:15 ET, and record the local submit time. Repeat on **5 normal sessions**: not early-close days, and not earnings days for KO or AAPL. That gives 10 F and 10 W fills.

**Record, per order:** `id`, `submitted_at`, `filled_at`, `filled_qty`, `filled_avg_price`, `status`. Then, after 09:46 ET so the free-plan SIP query is allowed:
- **Bar open:** `alpaca_raw.daily_bars([S], d, d, feed=SIP)` → `o`. This is the first eligible trade (FDT S1, S20).
- **Official open:** SIP trades for S from 09:29:50 to 09:31:00 ET via alpaca-py `StockTradesRequest` (not wrapped). Take the listing-exchange print with condition O (opening trade) and its condition Q duplicate (S26). NYSE for KO and Nasdaq for AAPL.
- **Pre-market NBBO at submit** and **NBBO at `filled_at`** via `StockQuotesRequest` (not wrapped), to answer which NBBO priced order F.

**Metrics:** latency = `filled_at` − 09:30:00 ET. gap_official_bps = 10,000 × (fill − official open) / official open. gap_bar_bps = 10,000 × (fill − bar open) / bar open. For buys, a positive gap is a cost. Report the median and the worst case for each order type.

**Reading the outcome:**

| Paper outcome | What it means | What it changes |
|---|---|---|
| F and W fill a few hundred ms after 09:30 at the NBBO ask; fill ≠ official open | Paper's model is "first NBBO after open", as documented (S27, S25) | This is how the paper stage will record fills. A backtest with `execution.fill_price = "open"` would need a slippage allowance at least the observed p90 of \|gap_bar_bps\| plus half-spread. That allowance key does not exist yet; creating it is for the execution ADR |
| F fills at the 09:00 pre-market NBBO | S21's "at the time the order was submitted" applies literally, at least on paper | F's price is known before the open. Record it for the ADR; still verify live |
| W or O fills exactly at the official open on paper | Contradicts S26 (paper has no auction) | Re-open this report; do not generalise to live |
| Any order does not fill by 09:31 | A paper queue or release delay | Record it; it affects the pre-open rebalance timing assumption |

**Paper is simulated.** It shows how the *paper stage* will behave. It cannot show whether live whole-share DAY orders get auction prices (2d, 2f, 2g), and it omits price improvement (S27). A small live test with the same parameters is the only way to answer the live question: for example 5 sessions × 1 share plus $5 notional, in one or two names. Whether to run it is **the owner's decision**. `execution.fill_price` stays `close` until the owner decides.

## Probe 1 results (owner run, 2026-09-25, #101)

Run with the owner's keys by team interlac on 2026-09-25 07:39 UTC, with the owner's permission: `scripts/probe_ca_depth.py` on branch `spike/101-probe-1-ca-depth` (commit 002f7c9; spike code, not merged). It makes the ten calls above, saves raw payloads outside the repo, and grades them offline. Raw payloads are kept by the owner, not in git.

**Verdict: complete back to 2016. ADR 0003's 2016 adjustment backfill horizon stands.** Every check below passes in every year from 2016 to 2025.

| Year | Symbol | Event | Alpaca ex-date and ratio | Result |
|---|---|---|---|---|
| 2016 | MNST | 3-for-1 split | 2016-11-10, 3 | matches |
| 2017 | ISRG | 3-for-1 split | 2017-10-06, 3 | matches |
| 2020 | AAPL | 4-for-1 split | 2020-08-31, 4 | matches |
| 2020 | TSLA | 5-for-1 split | 2020-08-31, 5 | matches (ground truth from memory) |
| 2021 | NVDA | 4-for-1 split | 2021-07-20, 4 | matches |
| 2021 | GE | 1-for-8 reverse split | 2021-08-02, 1/8 | matches (ground truth from a search summary) |
| 2022 | TSLA | 3-for-1 split | 2022-08-25, 3 | matches (ground truth from memory) |
| 2023 | GE | GEHC spin-off, 1 per 3 | 2023-01-04, `new_rate` 0.33333 | matches; Alpaca rounds rates to 5 decimals |
| 2024 | NVDA | 10-for-1 split | 2024-06-10, 10 | matches |

- **Cash dividends:** AAPL and KO return exactly 4 per year in every year 2016 to 2025. TSLA (negative control) returns none.
- **Events not in the ground truth, all real:** GE→WAB spin-off 2019-02-25 (`new_rate` 0.005371), GE→GEV spin-off 2024-04-02 (1 per 4), and NVDA's cash acquisition of MLNX (2020-04-27). The query returns mergers where a requested symbol is the acquirer, not only the acquiree.
- **The `start`/`end` window filters on `process_date`.** Every returned event has its `process_date` inside the requested year; some `ex_date`s fall outside it. Example: a GE dividend with ex-date 2020-12-18 and process date 2021-01-25 comes back in the 2021 query, not 2020. Consequence for ingest (T12): a window chosen by ex-date misses events processed after it ends. Pad the window past its end, or query by process date and filter on ex-date.
- **Payload fields are thinner before 2020.** Events from 2016 to 2019 carry only `ex_date` and `process_date`. From 2020 they also carry `record_date`, `payable_date` and, for splits, `due_bill_redemption_date`. No event in any year has a declaration or announcement date. Consequence for #83: an `announced_at` column (option 1) could never be filled from Alpaca, only from another source such as EDGAR 8-Ks.

## Sources
Sources S1 to S20 are in [2026-09-25-free-data-terms.md](2026-09-25-free-data-terms.md) (cited as FDT Sn). All new sources were seen on 2026-09-25.
- **S21** Alpaca, Fractional Trading: https://docs.alpaca.markets/us/docs/fractional-trading (Tier 1; re-fetched for the principal and pricing sentences not quoted in FDT S6)
- **S22** Alpaca Customer Agreement, V26.2026.07 (PDF): https://files.alpaca.markets/disclosures/library/AcctAppMarginAndCustAgmt.pdf. §26 p.14, §28 pp.15–16 (Tier 1)
- **S23** Alpaca Securities LLC, Held NMS Stocks and Options Order Routing Public Report, 2nd Quarter 2026 (generated 2026-07-16): https://files.alpaca.markets/disclosures/library/SEC+606a1+-+2026Q2.pdf (Tier 1); index at https://alpaca.markets/disclosures
- **S24** Alpaca Community Forum, "Fractional order execution", staff answer 2025-03-24: https://forum.alpaca.markets/t/fractional-order-execution/16562 (Tier 2)
- **S25** Alpaca Community Forum, "Frustration with the execution of Pre-Market Orders", staff answers 2024-03-05 to 2024-03-08: https://forum.alpaca.markets/t/frustration-with-the-execution-of-pre-market-orders/13787 (Tier 2)
- **S25b** Alpaca, Placing Orders (OPG, queued orders, fractional TIF table): https://docs.alpaca.markets/us/docs/orders-at-alpaca (Tier 1; FDT S7, re-fetched)
- **S26** Alpaca Community Forum, "OPG orders not filled at open price (live trading)", staff answers 2024-07-20/21: https://forum.alpaca.markets/t/opg-orders-not-filled-at-open-price-live-trading/14672 (Tier 2)
- **S27** Alpaca, Paper Trading: https://docs.alpaca.markets/us/docs/paper-trading (Tier 1; FDT S8, re-fetched for the "not simulated" list)
- **S28** Alpaca, 24/5 Trading: https://docs.alpaca.markets/us/docs/245-trading (Tier 1)
- **S29** Alpaca Use and Risk Disclosures, v.2.2020.11 (PDF): https://files.alpaca.markets/disclosures/library/UseAndRisk.pdf, p.1 (Tier 1)
- **S30** FINRA, 2023 Report, Fractional Shares: Reporting and Order Handling: https://www.finra.org/rules-guidance/guidance/reports/2023-finras-examination-and-risk-monitoring-program/fractional-shares (Tier 1; nothing on execution timing, disconfirmation log only)
- Probe ground truth (Tier 1 filings, located via search summaries): MNST 8-K ex-99.1 2016 https://www.sec.gov/Archives/edgar/data/0000865752/000110465916150144/a16-19907_1ex99d1.htm ; ISRG 8-K ex-99.1 2017-08-11 https://www.sec.gov/Archives/edgar/data/0001035267/000103526717000116/a20170811ex-991.htm ; NVDA 8-K 2021 https://www.sec.gov/Archives/edgar/data/1045810/000104581021000056/pr-may2021.htm ; NVDA 8-K 2024-05-22 https://www.sec.gov/Archives/edgar/data/1045810/000104581024000113/nvda-20240522.htm ; GEHC 10-Q 2023 https://www.sec.gov/Archives/edgar/data/1932393/000193239323000112/gehc-20230630.htm
- Lead not read: forum thread 11664 "Opening and closing market prices in paper trading" (fetch failed).
