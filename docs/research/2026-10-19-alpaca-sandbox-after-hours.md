# Research Report: What Alpaca paper does after the close, from the sandbox's after-hours probes

**Brief:** #1909 (addendum to [the first sandbox report](2026-10-19-alpaca-sandbox-probe.md), #1892)  ·  **Date:** 2026-10-19  ·  **Status:** COMPLETE  ·  **Agent/model:** team sandboxdoc, claude-sonnet-5-5; read-only analysis of recorded broker responses

## Answer
**Verdict:** SUPPORTED  ·  **Confidence:** high for each recorded response (primary evidence, one account, one evening), low for anything that depends on the symbol or the day.
This addendum **supersedes the first report's "pending: closed-market edge run" section** and adds the long books' order shape (T226) as Alpaca answers it. After the close (16:23 to 16:25 ET) Alpaca queues a market or limit DAY buy for the next session, fills extended-hours DAY limits at once, and fills in the intent on a buy sent without one. It refuses the same things it refused in the day session, with the same codes. The first report is unchanged.

## Evidence
Files are in `~/tradepartner-probes/sandbox/results/` (outside the repo): `batch4-20261019T202256Z.jsonl` (long-intent shape, closed market), `batch4-20261019T202344Z.jsonl` (the same script's extended-hours part), `batch2-20261019T202448Z.jsonl` (`--only ext_hours`), `batch3-20261019T202507Z.jsonl` (`--only edge`, closed market). All four were run at 20:22 to 20:25 UTC, after the 16:00 ET close and inside the extended session (F: last 12.18, bid 11.76, no ask). Tier is "recorded" (our request, Alpaca's response) for every row; out-of-sample and net-of-costs do not apply.

### 1. Closed market: the long books' order shape (batch 4)
| Claim | Source (label) | Key figure (Alpaca's answer) | Affects |
|---|---|---|---|
| A market DAY buy with `buy_to_open` by **qty** is accepted after the close and queued | b4 `queue.qty_buy_to_open` | `status=accepted`, qty 1, `filled_qty=0`, `expires_at=2026-10-20T20:00:00Z` (next day's close) | the daily book's first buys tomorrow: a pre-open submit is accepted and waits |
| The same by **notional** is accepted and queued | b4 `queue.notional_buy_to_open` | notional 10, `status=accepted`, same `expires_at` | notional buys of the long books |
| A buy with **no** `position_intent` comes back with `position_intent=buy_to_open`, filled in by Alpaca | b4 `queue.qty_no_intent` | accepted, `position_intent=buy_to_open` | T226: the pre-T226 shape (no intent) still works; the new shape (explicit `buy_to_open`) matches what Alpaca would infer |
| `sell_to_close` while flat is refused | b4 `queue.sell_to_close_flat` | 422 `42210000`: "position intent mismatch, inferred: sell_to_open, specified: sell_to_close" | T235's refusal allowlist (this message is the same one the first report recorded in the day session; it is not typed yet) |
| Cleanup | b4 `cleanup.orders` | 3 queued orders cancelled, 0 left open, no positions | none |

### 2. Extended hours (batch 4 part 2 and batch 2 `ext_hours`)
| Claim | Source (label) | Key figure (Alpaca's answer) | Affects |
|---|---|---|---|
| An extended-hours DAY limit `buy_to_open` fills | b4 (20:23:45) `ext.buy_to_open` | limit 12.30, filled 12.19, `extended_hours=True`, `expires_at=2026-10-20T00:00:00Z` | intraday and extended-hours work |
| An extended-hours DAY limit `sell_to_close` fills | b4 (20:23:45) `ext.sell_to_close` | limit 11.76 (the bid), filled 12.18, position back to flat | intraday work; a marketable limit is the way to exit in extended hours |
| Extended hours takes DAY or GTC limits only | b2 `ext.limit_day`, `ext.limit_gtc` | both accepted (`new`); GTC `expires_at=2027-01-16T01:00:00Z` | order builders |
| IOC is refused, with a different message than in the day session | b2 `ext.limit_ioc` | 422 `42210000`: "ioc orders are only accepted during market hours" (the day session said "extended hours order must be DAY or GTC limit orders") | refusal messages differ by clock: match on code plus a message family, not one string (T235) |
| A market order is refused | b2 `ext.market` | 422 `42210000`: "extended hours order must be DAY or GTC limit orders" | order builders |
| A stop order is refused | b2 `ext.stop` | 422 `42210000`: "stop orders are not eligible for extended hours trading" | exits that rely on stops do not protect in extended hours |
| A fractional extended-hours limit is accepted | b2 `ext.fractional_limit` | qty 0.5, `new`, then cancelled | fractional buys |

### 3. Closed-market edge cases (batch 3 `edge`; fills the first report's pending section)
| Claim | Source (label) | Key figure (Alpaca's answer) | Affects |
|---|---|---|---|
| A DAY **limit** buy sent when the market is closed (outside the extended session's quote) is accepted and queued | b3 `edge.closed_limit_day` | `status=accepted`, limit 10.96, `expires_at=2026-10-20T20:00:00Z`; cancel returns `canceled` | the daily book's queued orders; reconcile must treat `accepted` as open |
| A DAY **market** buy is accepted and queued | b3 `edge.closed_market_day` | `status=accepted`, `expires_at=2026-10-20T20:00:00Z` | same |
| Unknown symbol | b3 `edge.unknown_symbol` | 422 `42210000`: `asset "ZZZQXQ" not found` | T235 allowlist |
| Untradable asset (`tradable=False`, RENO, OTC) | b3 `edge.untradable_buy` | 422 `42210000`: `asset "RENO" is not tradable` | T235 allowlist |
| Insufficient buying power | b3 `edge.insufficient_buying_power` | **403** `40310000`: "insufficient buying power" (order $7,999,781 vs 399,989) | refusal typing; T235 |
| `qty` 0 | b3 `edge.qty_zero` | 422 **`40010001`**: "qty must be > 0" | **#1884**: a third message on the code the adapter maps to a duplicate `client_order_id` |
| Sub-penny limit | b3 `edge.sub_penny_limit` | 422 `42210000`: "invalid limit_price 10.965. sub-penny increment does not fulfill minimum pricing criteria" | price rounding in the order builders |
| Odd-lot marketable limit | b3 `edge.odd_lot` | skipped: needs regular hours (the day-session result is in the first report) | none |

### What this means for the builds
- **Tomorrow's daily first buys:** orders sent before the open are accepted and queued (`accepted`), with a next-day-close `expires_at`; the intent may be sent explicitly as `buy_to_open` or left out.
- **#1884:** `40010001` is now seen for a bad intent, a zero qty and two trail fields (first report), plus a duplicate `client_order_id`; map by code **and** message.
- **T235's refusal allowlist:** the `position intent mismatch` 422 and the unknown/untradable-asset 422s share code `42210000` with everything else; the 403 `40310000` covers insufficient buying power. Match on message.
- **Intraday work:** extended-hours limits fill; stops and markets are refused; IOC's message depends on the clock.

## Disconfirmation
- Searches run: all four result files read in full against the first report's rows.
- What was found against: none. The after-hours answers agree with the day session except the IOC message.

## Caveats & gaps
- One account, one evening, one symbol (F) for orders; the extended-hours fills happened with no ask quote, so fill prices are one sample.
- The first batch 4 run skipped its extended-hours part (no two-sided quote); the second run did it. The odd-lot probe needs regular hours.
- The overnight borrow, interest and fee check is still not run.

## UNVERIFIED items
- Whether a queued `accepted` order fills at the open at the open's price or later (the orders were cancelled before the open).
- The refusal text for a shortable, non-ETB name, and whether `GET /v1/locates` exists on the paper host (from the first report).

## Follow-up questions (not answered here)
- Should the daily book's pre-open orders carry an explicit `buy_to_open` (T226 shape) now that the omitted intent is confirmed to be inferred the same way?

## Sources
- [First sandbox report](2026-10-19-alpaca-sandbox-probe.md) and the probe safety rules `~/tradepartner-probes/sandbox/README.md` (outside the repo).
- Recorded responses: `~/tradepartner-probes/sandbox/results/batch4-20261019T202256Z.jsonl`, `batch4-20261019T202344Z.jsonl`, `batch2-20261019T202448Z.jsonl`, `batch3-20261019T202507Z.jsonl`.
- Plans: [paper-trading](../plans/paper-trading.md) (T226, T235); issues #1884, #1892, #1909.
