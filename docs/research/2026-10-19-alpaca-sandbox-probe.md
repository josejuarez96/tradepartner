# Research Report: What Alpaca paper actually does, from the sandbox account's first three probe batches

**Brief:** #1892 (owner decision of 2026-10-19; feeds #1872, #1884, T226 to T237)  ·  **Date:** 2026-10-19  ·  **Status:** INCOMPLETE (the closed-market `edge` run is pending)  ·  **Agent/model:** team sandboxdoc, claude-sonnet-5-5; read-only analysis of the recorded broker responses

## Decision recorded
On 2026-10-19 the owner decided to use a **third Alpaca paper account, "the sandbox"**, only to find out how Alpaca really behaves before we build a feature on a guess: shorting, position intents, order types, time in force, extended hours, brackets/OTO/OCO, fractional orders, options, intraday round trips and closed-market edge cases. Its rules:
- It is **separate from the two real paper books** (main, daily) and **never holds a strategy book**. Nothing in `src/` reads its keys.
- It is reached only by **throwaway probe scripts outside the repo** (`~/tradepartner-probes/sandbox/`; the rules are in its `README.md`). Keys are `ALPACA_SANDBOX_API_KEY` / `ALPACA_SANDBOX_API_SECRET`, held in `.env` only.
- The scripts talk to the **paper host only** (`paper-api.alpaca.markets`; GET-only to `data.alpaca.markets` for quotes), cap each order at about $500 (one deliberate over-cap buying-power probe), **refuse to start if the keys equal a book's keys**, record every request and response, and **clean up to flat** at the end of a run.
- The recorded responses are the evidence; the repo links them by name and does not copy them.

Account state at preflight (2026-10-19 17:27 UTC, `batch1`, `preflight.account`): ACTIVE, created 17:12 UTC the same day, equity and cash $100,000, `multiplier` 4, `buying_power` 400,000, `regt_buying_power` 200,000, `options_approved_level` and `options_trading_level` 3, `options_buying_power` 100,000, `shorting_enabled` true. `daytrade_count`, `pattern_day_trader` and `daytrading_buying_power` come back absent (None) everywhere.

## Answer
**Verdict:** SUPPORTED (for what was run; the after-close `edge` section is not)  ·  **Confidence:** high for each recorded response (primary evidence, one account, one day), low for anything that varies by symbol or day (rates, fills, borrow).
Alpaca paper shorts and covers whole shares cleanly and refuses every fractional short, but its refusal codes are shared across different causes (`42210000` for most, `40010001` for both a duplicate `client_order_id` and a bad `position_intent`), so the adapter must map a refusal by code **and** message. It infers `position_intent` when we omit it and rejects an explicit intent that disagrees with the position. Stops, brackets, OTO and OCO behave as the docs say, with a few exceptions below (PATCH of an OTO is accepted, a take-profit cancel cancels its stop sibling, a buy-stop below the market is refused). Options trade with DAY and GTC only, whole contracts only, and a two-leg vertical fills as one `mleg` order. No fee, borrow or interest activity appeared in the run.

## Evidence
Files are in `~/tradepartner-probes/sandbox/results/` (outside the repo): `batch1-20261019T172717Z.jsonl` (shorting, 17:27 UTC, 12 minutes of regular hours), `batch2-20261019T173228Z.jsonl` (orders, F, 17:32), `batch3-20261019T174345Z.jsonl` (options and intraday, 17:43). The tier column is "recorded" for every row: our own request and Alpaca's own response, labelled as in the file. Out-of-sample and net-of-costs do not apply to behaviour probes.

### 1. Shorting (batch 1)
| Claim | Source (label) | Tier | Key figure (Alpaca's answer) | Affects |
|---|---|---|---|---|
| A 1-share market DAY short of an ETB name fills at once and is returned as `sell_to_open` even though we sent no intent | b1 `short.open_1_share_market_no_intent` | recorded | AAPL, 1 share, `pending_new>filled`, fill 342.02, `position_intent=sell_to_open` | T226, T230 |
| The short position reads `side=short`, negative `qty`, negative `qty_available`, negative `market_value` and `cost_basis` | b1 `short.position_while_short` | recorded | `qty=-1`, `qty_available=-1`, `market_value=-342.0201`, `cost_basis=-342.02` | T226, T232, T233 |
| Account while short: cash rises by the proceeds, `short_market_value` is negative, margin is charged | b1 `short.account_while_short` | recorded | cash 100,342.02; `short_market_value=-342.03`; `initial_margin=171.01`; `maintenance_margin=102.61`; buying power 400,000 to 399,589.54 | T232, T233, T234 |
| A fractional short is refused, both from flat and when adding to an existing short | b1 `fractional.short_from_flat`, `short.fractional_add_while_short` | recorded | 422, code `42210000`, "fractional orders cannot be sold short" | T230 ("no short request is fractional"), T227, T235 allowlist |
| A whole-share short can be covered fractionally (0.5 then 0.5, both `buy_to_close`) | b1 `short.fractional_partial_cover`, `short.cover_buy_to_close` | recorded | both filled (342.044, 342.036); position gone | T230 (we cover whole shares anyway; fractional covers are allowed, not needed) |
| A plain buy (no intent) against a short is accepted and inferred as `buy_to_close` | b1 `intent.cover_buy_no_intent` | recorded | filled 342.06, `position_intent=buy_to_close`, position gone | T226, T230 |
| An explicit intent that disagrees with the position is a hard 422 | b1 `intent.sell_to_close_while_flat`, `intent.buy_to_close_while_flat` | recorded | 422 `42210000`: "position intent mismatch, inferred: sell_to_open, specified: sell_to_close" (and `buy_to_open` vs `buy_to_close`) | T226, T230, #1884, T235 allowlist |
| An invalid `position_intent` value answers with the **same code as a duplicate client_order_id** | b1 `intent.bogus_value` | recorded | 422 `40010001`: "invalid position_intent specified" | #1884 (map 40010001 by message too) |
| A non-shortable name is refused at the order, with the asset flags already showing it | b1 `flags.BYND`, `htb.short_BYND` | recorded | `shortable=False`, `easy_to_borrow=False`; 422 `42210000`: `asset "BYND" cannot be sold short` | T231, T234, T235 (`skip_not_borrowable`) |
| Short margin is per asset | b1 `flags.*` | recorded | `margin_requirement_short`: 30 for AAPL, MSFT, SPY, QQQ, CRCL, FIG, KLAR, BRK.B, F; 100 for TQQQ (long 75) and GME (long 100) | T231, T234 |
| Account fields to read for a short | b1 `short.account_*` | recorded | `short_market_value`, `regt_buying_power`, `initial_margin`, `maintenance_margin`, `last_maintenance_margin`, `multiplier`, `shorting_enabled` | T226, T233 |
| Fee, interest and borrow activities: none seen | b1 `activities.*` | recorded | 5 `FILL` activities in the run; fee-like types (FEE, CFEE, INT and the rest) returned 0 | T220 borrow fee (paper charges none), T232 |
| A real hard-to-borrow (shortable but not ETB) name was **not** found | b1 `flags.*` | recorded | all 11 shortable names probed were also ETB; only BYND (not shortable) was refused | T227 still owes this |

### 2. Orders (batch 2, symbol F, about 12.17)
| Claim | Source (label) | Tier | Key figure (Alpaca's answer) | Affects |
|---|---|---|---|---|
| Limit, cancel, a second cancel and PATCH | b2 `limit.*`, `replace.*` | recorded | PATCH of qty and price returns a **new order id**; the old one is `replaced` with `replaced_by` set; PATCH `tif` day to gtc also replaces; PATCH of a cancelled order is 422 `42210000` "order is not open"; PATCH of a notional order is 422 "cannot replace limit_price in notional limit order" | order-path code that tracks ids; journal |
| Buy-stop above the market rests; buy-stop **below** the market is refused | b2 `stops.stop_buy_above`, `stops.stop_buy_below_market` | recorded | 422 `42210000` "stop price must be greater than current price" | stop-cover logic on a short (stop above market only) |
| Stop-limit buy rests; trailing stops by percent and by price rest and can be PATCHed; both trail fields at once are refused | b2 `stops.*` | recorded | trailing stop returns `hwm` and a computed `stop_price`; PATCH `trail_percent` 5 to 7 makes a new order; both fields: 422 `40010001` "trailing stop orders cannot specify both trail_price and trail_percent" | #1884 (a second 40010001 text); T226 |
| Time in force: DAY, GTC, CLS accepted; IOC and FOK accepted then cancelled at once (below market); OPG refused inside the session | b2 `tif.*` | recorded | OPG 403 `40310000` "opg orders must be submitted after 7:00pm and before 9:28am"; DAY `expires_at` 2026-10-19T20:00:00Z (the close); GTC `expires_at` 2027-01-15T21:00:00Z (90 days) | order TIF vocabulary; reconcile (expiry) |
| Market GTC fills when open | b2 `tif.market_gtc` | recorded | filled 12.18 | none |
| Extended hours: limit with DAY or GTC accepted; IOC, market and stop refused | b2 `ext.*` | recorded | 422 `42210000` "extended hours order must be DAY or GTC limit orders"; "stop orders are not eligible for extended hours trading"; a fractional extended-hours limit was accepted; a DAY extended-hours order's `expires_at` is 2026-10-20T00:00:00Z (20:00 ET) | later intraday/extended-hours work |
| Bracket with a limit entry: both legs `held`; PATCH of the entry makes a new parent with the legs carried over; cancelling the parent cancels both legs | b2 `bracket.limit_entry_resting*` | recorded | legs: take-profit `limit` and `stop_limit` (when a stop-limit leg was asked for) | exits for any bracketed entry |
| Bracket refusals | b2 `bracket.bad_prices`, `bracket.ext_hours`, `bracket.fractional` | recorded | 422 "take_profit.limit_price must be > stop_loss.stop_price"; 403 `40310000` "bracket orders do not support extended hours trading"; 422 "fractional orders must have order_class=simple" | order builders |
| Market-entry bracket: fills, take-profit leg goes `new`, stop leg `held`; cancelling the take-profit leg **also cancels the stop sibling** | b2 `bracket.market_entry*`, `bracket.sibling_after_tp_cancel` | recorded | legs `[('limit','canceled'), ('stop','canceled')]` | exit handling: a manual TP cancel removes the stop protection |
| OTO with a stop-loss: a **PATCH of the entry was accepted** (new parent id), contrary to the docs' "unsupported" | b2 `oto.patch` | recorded | replaced; the held stop leg carried over | none (do not rely on it) |
| Sell-side exits (stop, stop-limit, trailing % and $, OCO) all rest; each reserves shares | b2 `sell_side.*` | recorded | with five exits resting on 7 shares, `qty_available` fell 7 to 2; OCO returned a limit parent plus one held stop leg and could be PATCHed | reconcile (`qty_available` is not `qty`) |
| A sell larger than `qty_available` is refused | b2 `sell_side.sell_over_available` | recorded | 403 `40310000` "insufficient qty available for order (requested: 3, available: 2)" | T230 (order of cancels before closes), T235 |
| Fractional grid (buy side, DAY): market, limit, stop, stop-limit accept qty 1, qty 0.5 and notional $10; trailing stop accepts only whole qty | b2 `frac.*` | recorded | trailing stop fractional/notional: 422 "fractional orders must be market, limit, stop, or stop_limit orders"; notional $10 market filled 0.821005917 at 12.168 | fractional buys |
| Fractional refusals | b2 `frac.limit_gtc`, `frac.notional_stop_gtc`, `frac.qty_too_precise`, `frac.notional_tiny` | recorded | "fractional orders must be DAY orders"; "stop/stop_limit fractional GTC orders are not enabled"; "qty must be > 1e-9"; "notional amount must be >= 1.00" | fractional buys |

### 3. Options and intraday (batch 3, SPY options and F)
| Claim | Source (label) | Tier | Key figure (Alpaca's answer) | Affects |
|---|---|---|---|---|
| Options are enabled at level 3 on a new paper account | b3 `options.level` | recorded | approved 3, trading 3, options buying power 99,999.8 | later options work |
| Contract list for 1 to 10 day expiries | b3 `options.contracts` | recorded | 2,424 SPY contracts; fields include `strike_price`, `expiration_date`, `size` 100, `style` american, `open_interest`, `close_price` | later options work |
| A 1-lot limit at the ask fills; the fill can beat the limit | b3 `options.buy_call`, `options.close.*` | recorded | call limit 2.03 filled 2.01; sell of 2 with limit 2.03 filled 2.07; options DAY `expires_at` 20:15Z (4:15 pm ET) | options fills model |
| A 2-leg debit call vertical goes as one `mleg` order and fills as a unit | b3 `options.vertical_mleg` | recorded | limit 0.51 filled 0.51; both legs `filled`; the parent's `side` is empty | later options work |
| Option positions | b3 `options.*.position`, `options.positions` | recorded | `asset_class=us_option`, `market_value` and `cost_basis` already multiplied by 100 (qty 1, current 2.02, market_value 202); short leg `qty=-1` | later options work |
| Options refusals: GTC is accepted; fractional qty, extended hours, notional and IOC are refused | b3 `options.gtc`, `options.fractional_qty`, `options.ext_hours`, `options.notional`, `options.ioc` | recorded | 422 `42210000`: "qty must be integer for options trading", "extended hours is not supported for options trading", "notional orders are not supported for options trading", "order_time_in_force provided not supported for options trading" | later options work |
| Intraday round trip: buy 1 F then sell 1 F the same day | b3 `intraday.*` | recorded | both fill at market; `daytrade_count`, `pattern_day_trader` and `daytrading_buying_power` stay absent, `trading_blocked` false | PDT is not a gate (consistent with the 2026-07-06 field removal in `alpaca-github-notes.md`) |
| Edge refusals | b3 `edge.*` | recorded | unknown symbol: 422 `42210000` `asset "ZZZQXQ" not found`; untradable asset (`tradable=False`, e.g. FTCHQ): 422 `asset "FTCHQ" is not tradable`; insufficient buying power ($7,999,986 vs 399,999): **403** `40310000` "insufficient buying power"; qty 0: 422 **`40010001`** "qty must be > 0"; sub-penny limit 10.965: 422 `42210000` "sub-penny increment does not fulfill minimum pricing criteria" | #1884 (third 40010001 text), T226 refusal typing |
| An odd-lot marketable limit fills at the ask, in more than one piece | b3 `edge.odd_lot_marketable_limit` | recorded | 37 shares, limit 12.25, filled 12.19 (ask 12.19), lifecycle `new>partially_filled>filled` | fill model; partial-fill handling |
| Closed-market behaviour of a DAY limit and a DAY market order | b3 `edge.outside_hours_day` | recorded | **pending: closed-market edge run** (skipped: the market was open at 17:44 UTC; the owner reruns `--only edge` after 16:00 ET) | order path while closed |

### 4. Cross-cutting
| Claim | Source | Tier | Key figure | Affects |
|---|---|---|---|---|
| Rate limit header on trading calls | any `http` record's `response_headers` | recorded | `x-ratelimit-limit: 200`, `x-ratelimit-remaining`, `x-ratelimit-reset` | adapter backoff |
| Statuses seen in order lifecycles | all batches | recorded | `pending_new`, `new`, `partially_filled`, `filled`, `canceled`, `replaced`, `held` | the status mapper must be total (see `alpaca-github-notes.md`, section 2) |
| Error codes by cause | all batches | recorded | `42210000` is the default validation refusal; `40010001` appears for a bad intent, a zero qty, two trail fields (and, per #1884, a duplicate `client_order_id`); `40310000` for insufficient buying power, insufficient qty, OPG window and bracket extended hours | #1884; T226's refusal bodies; T235's allowlist |

### Builds affected, in one place
- **T226 (done, #1888) and #1884:** refusal typing by code **and** message; the `position intent mismatch` 422 is not yet typed (decide with T235's allowlist).
- **T227 (owner):** most of its "short refused", "fractional refused" and "cover" recordings now exist in the sandbox files, but they came from the sandbox, not the short book's account, so the plan's fixtures still need the book's own recordings; the HTB (shortable, not ETB) case and `GET /v1/locates` remain unrecorded.
- **T230:** whole-share shorts only; a plain buy covers a short (inferred `buy_to_close`); send an intent only when computed from the position; cancel resting exits before a close (`qty_available`).
- **T231, T233, T234:** per-asset `margin_requirement_short` (30 or 100); the short position and account fields above.
- **T235:** the allowlist entries should match `42210000` plus the message, not the code alone.
- **T220:** paper shows no borrow fee on an ETB short within a session; the overnight check (`--leave-short`, then `--only followup`) has not been run.
- **Later intraday and options work:** the extended-hours, TIF, bracket, options and edge rows above.

## Disconfirmation
- Searches run: read all three result files in full (every labelled note) and `alpaca-github-notes.md` (the same-day survey of the alpacahq repos and docs) against each claim.
- What was found against: three docs claims did not hold on the day. The OTO PATCH was accepted (docs: unsupported). `daytrade_count` is absent (explained by the 2026-07-06 removal, not a paper quirk). No HTB name was available to probe, so the HTB refusal text is unknown.

## Caveats & gaps
- One account, one day, one 12-minute window per batch, regular hours; prices and fills are not representative of other days or names. Paper fills follow Alpaca's paper rules (marketable fills, random partial fills), not a real book.
- The overnight borrow, interest and fee check was not run; an empty fee list inside a session does not show that paper never charges one.
- Batch 3's closed-market `edge` section is **pending: closed-market edge run** (the owner runs it after 16:00 ET). No `batch3-*.jsonl` newer than `20261019T200000Z` existed when this was written; update this report when it lands.
- The result files live outside the repo and are not versioned; quoted messages are copied verbatim from them.

## UNVERIFIED items
- Whether a DAY limit or market order queues or is refused while the market is closed (pending run).
- The refusal text for a shortable, non-ETB name, and whether `GET /v1/locates` exists on the paper host.
- Overnight borrow fee, margin interest and where any such charge posts.

## Follow-up questions (not answered here)
- Do the T227 recordings need the book's own account, or may the sandbox's recordings stand for the refusals (owner decision)?
- Should the `position intent mismatch` 422 get its own type before T235 (an allowlist decision)?

## Sources
- Probe safety rules: `~/tradepartner-probes/sandbox/README.md` (outside the repo).
- Recorded broker responses: `~/tradepartner-probes/sandbox/results/batch1-20261019T172717Z.jsonl`, `batch2-20261019T173228Z.jsonl`, `batch3-20261019T174345Z.jsonl`.
- Alpaca docs and SDK survey of the same day: `~/tradepartner-probes/sandbox/alpaca-github-notes.md`.
- Plans: [paper-trading](../plans/paper-trading.md) (T226 to T237), [backtest](../plans/backtest.md) (T218 to T224); issues #1872, #1884, #1885 (PR #1888), #1892.
