# Alpaca paper broker facts — T48b working report

**Status:** Incomplete, 2026-09-27. The account recording has not run: this
worktree has no `ALPACA_PAPER_API_KEY` or `ALPACA_PAPER_API_SECRET` configured,
and today is outside regular market hours. This report records documented facts
and the observations still required; it is not an acceptance of the paper
adapter's behavior. The T48b plan checkbox remains open.

## Documented limits and endpoints

| Question | Documented answer | Source | Paper observation |
|---|---|---|---|
| Fractional order quantity precision | Up to 9 decimal places for `qty` and `notional` | [Alpaca Fractional Trading](https://docs.alpaca.markets/us/docs/fractional-trading), Supported Order Types | Pending a recorded accepted order |
| `client_order_id` length | At most 128 characters on `POST /v2/orders` | [Create an Order](https://docs.alpaca.markets/us/reference/postorder), Body Params | Pending; the recorder's IDs are shorter |
| Fractional minimum | As little as $1 worth of shares | [Alpaca Fractional Trading](https://docs.alpaca.markets/us/docs/fractional-trading), opening paragraph | Pending, including a whole-position sell below $1 |
| Trading request limit | Alpaca's [support answer](https://alpaca.markets/support/usage-limit-api-calls) states 200 requests/minute/account (dated 2022); a [staff forum answer](https://forum.alpaca.markets/t/is-there-a-way-to-increase-the-200-min-api-call-limit-for-the-trading-endpoint/18110/2) repeats this for paper and live. `alpaca.trading_requests_per_minute=150` is below that published number. | The support and staff pages, **not** the Market Data API limit | Verify this account's response headers or 429 behavior; current docs do not guarantee the value for all accounts |
| Fill history endpoint | `GET /v2/account/activities/FILL` under `paper-api.alpaca.markets`; paginated with `page_size` up to 100 and `page_token` | [Retrieve Account Activities of Specific Type](https://docs.alpaca.markets/us/reference/getaccountactivitiesbyactivitytype-1); `AlpacaTradingRaw.list_fill_activities` | Pending recorded `fill_activities.json` |

The two documented structural limits are defaulted in `AlpacaConfig` by this
PR: `quantity_decimals=9`, `client_order_id_max_length=128`. The paper
recording must confirm accepted behavior before the task is marked complete.
The minimum and reconciliation tolerances retain their provisional defaults
until the direct observations below resolve them.

## Paper observations required by spec requirement 2

| Question | Recording or measurement needed | Result |
|---|---|---|
| Submit, accept, fill, partial fill, reject, expire, cancel; positions, account, open orders, fills, assets | Run `python -m tradepartner.cli_record paper SYMBOL` with paper keys on a flat account during regular hours; inspect scrubbed fixtures. Script covers the common paths, but partial fill and expiration may need an additional safe owner protocol. | Pending |
| Same-session sale proceeds in `account.cash` | Compare `account` immediately before and after the sale fill, including unsettled cash. If proceeds are absent, amend spec requirement 3 before T60d. | Pending |
| Cash rounding per fill | Compare each fill's quantity × price with account cash deltas; document cent rounding and accumulated error to set `risk.reconcile_cash_tolerance`. | Pending |
| Duplicate `client_order_id` after a terminal order and retention window | Replay the recorded first ID after it fills; a later session is needed to bound the retention window. | Pending |
| Whole-position sell below the trading minimum | On an existing tiny paper position, submit the complete quantity and record acceptance/refusal; do not confuse a notional buy minimum with a position exit. | Pending |
| Fractional sell of a non-fractionable asset | The recorder buys one whole share, then attempts a 0.5-share sell and flattens. | Pending |
| Split, cash dividend, spin-off, cash merger on a held paper name | Observe each event in the paper account and match activities/positions/cash. [#86's report](2026-09-25-alpaca-open-and-depth.md) records Alpaca's statement that paper does not simulate dividends, but no account observation yet. | Pending; may need separate event dates |
| CUSIP on `Asset` | Inspect the scrubbed asset responses for the field. | Pending |
| Non-fractionable count in the current universe | Query the current universe's asset records, count `fractionable=false`, and name the universe cutoff used. | Pending |

## Recording and review gate

The owner runs the existing guarded recorder with a non-fractionable, tradable
symbol on a **flat paper account** during regular hours, at least 30 minutes
before close. It uses only paper credentials, refuses an unexpected account
state, then attempts to flatten. Check `positions_after` and
`open_orders_after` before treating its output as evidence. The fixtures must
be scrubbed of keys, emails, account IDs and account numbers and pass the
scrub test. Do not infer an acceptance from an unexercised branch or an API
example. Fill in every result above, amend ADR 0010 and the paper spec with
confirmed values, then run the T48b tests and safety review.
