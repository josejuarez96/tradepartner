# Research Report: Alpaca paper broker facts (T48b)

**Brief:** issue #298 (plan task T48b; spec [paper-trading](../specs/paper-trading.md) req 2)  ·  **Date:** 2026-10-08  ·  **Status:** COMPLETE for what one recording can show. Every req 2 question is answered below or marked **not observed** with what would show it.  ·  **Agent/model:** team paperfacts, claude-opus-5-5. It replaces the 2026-09-27 working draft on the superseded PR #299, whose documented sources are kept here.

## Recording

On 2026-10-08 at 11:10 ET (15:10 UTC), with the owner's approval on #298, the orchestrator ran `python -m tradepartner.cli_record paper OCGN` on a flat paper account. The script is the fixed T48 script in `src/tradepartner/cli_record.py` (`_record_paper`). It used KO, which is fractionable, and OCGN, which is tradable and not fractionable. It ran these steps in order:

1. A $5 notional buy of KO (`-1`).
2. A buy of 1 KO share (`-2`).
3. A sell of 0.5 KO (`-3`).
4. A positions read.
5. A sell of the held KO quantity plus 0.5 (`-4`).
6. A resting limit buy of 1 KO at 90% of the fill price (`-5`), then its cancel.
7. A replay of order `-1`'s `client_order_id`.
8. A buy of 1 OCGN share (`-6`).
9. A sell of 0.5 OCGN (`-7`).
10. A flatten: one sell per position (`-8` KO, `-9` OCGN).
11. Account, positions, open-orders and fill-activity reads.

The account ended flat (`positions_after.json` and `open_orders_after.json` are both `[]`).

The 17 responses are in `tests/fixtures/alpaca/paper/`. They are scrubbed by the recorder: the account `id` and `account_number` read `<scrubbed>`, and `tests/test_fixture_scrub.py` passes over them. Their recording time is in `tests/fixtures/recorded_at.json`. The ids have the recorder's own format, `rec20261008151015-<n>`. They predate the ADR 0015 book token, and per the plan line's 2026-10-08 gate they stay opaque strings to the adapter tests.

**Grades.** **O**: observed in a named fixture of this recording. **D**: documented by Alpaca (sources listed below, all seen 2026-09-27) and not contradicted by the recording. **N**: not observed; the line names what would show it. A paper observation describes Alpaca's paper simulator only, not live execution ([#86's report](2026-09-25-alpaca-open-and-depth.md)).

## Answers to req 2

| # | Question | Answer | Grade | Evidence |
|---|---|---|---|---|
| 1 | Accepted fractional quantity precision | **9 decimal places.** The $5 notional buy filled 0.057436865 shares. The flatten's quantity sell of 0.557436865 was accepted and filled. The position read showed 0.557436865, exactly the sum of the fills. | O + D | `buy_fractional.json`, `flatten.json`, `positions_held.json`, `fill_activities.json`; S1 |
| 2 | `client_order_id` length limit | **128 characters**, as documented. The recorded ids are 19 characters long, so the limit itself was not exercised. Also not exercised: the `:` that a secondary class's id carries (`tests/execution/test_ids.py`) and the ADR 0015 id format. A submit with a 128-character id and one with a `:` would show both. | D (N at the limit) | S2; `buy_fractional.json` |
| 3 | Does `cash` reflect same-session sale proceeds? | **Yes.** The account was flat before (`cash` 100005.83) and after (`cash` 100005.82). In between were three buys and three sells in the same session. Their exact net cash effect is −$0.0078. The sells' proceeds total $92.85 and settle T+1, so had they been left out of `cash`, the after reading would be about 99913, not 100005.82. **Spec req 2's cash clause stands as written; T71's gate is answered and no ADR 0010 or spec rewrite is needed.** | O | `account_before.json`, `account_after.json`, `fill_activities.json` |
| 4 | Does `cash` net the notional of the account's own non-terminal buy orders? | **Not observed.** The account was not read while the resting limit buy (`-5`, 1 KO at $78.19) was open. An account read between that submit and its cancel would show it. The wrapper's "never borrows" claim (ADR 0010 item 6) does not rest on the answer. If `cash` does not net open buys, the reserve is what stops a double spend. If it does, the reserve counts them twice and the run under-invests, which still never borrows. | N | `resting.json`, `resting_cancelled.json` |
| 5 | Cash rounding per fill | **`cash` is reported in whole cents. The recording fits round-to-nearest-cent.** The six fills' exact net is −0.007770 and `cash` moved −0.01, so the residual is $0.0022. Rounding each fill to the nearest cent gives −0.01 (4.99, 86.88, 0.99 out; 43.44, 48.43, 0.98 in), and so does rounding the total. Truncating each fill gives 0.00, which does not match. Six fills cannot tell rounding per fill from rounding of the running balance. No fee was charged (`accrued_fees` 0, `pending_reg_taf_fees` 0). | O | as #3 |
| 6 | Does the duplicate-id check cover terminal orders, and for how long? | **It covers a filled order.** Order `-1` filled at 15:10:18. Replaying its id a few seconds later was refused with HTTP 422, `{"code":40010001,"message":"client_order_id must be unique"}`. **How long it is kept was not observed.** A replay of a recorded id in a later session, and again weeks later, would bound it. Replay (ADR 0007) dedupes through `get_order` before any submit, so it does not depend on the broker's check. | O (N on duration) | `duplicate_client_order_id.json`, `buy_fractional.json` |
| 7 | Is a whole-position quantity sell below `risk.min_order_notional` accepted? | **Yes, for a whole-share position.** The flatten sold the whole 1-share OCGN position at $0.9808, a $0.98 order, below the $1.00 minimum, and it filled. A fractional position worth less than $1 was not tried. Selling one would show whether the fractional minimum applies to sells. | O (N fractional) | `flatten.json` (`-9`), `fill_activities.json` |
| 8 | `risk.min_order_notional` | **1.0 confirmed.** Alpaca documents a $1 fractional minimum. The $5 notional buy was accepted. No buy under $1 was tried, so the minimum is documented, not observed. | D | S3; `buy_fractional.json` |
| 9 | Is a fractional-quantity sell of a non-fractionable asset accepted? | **No. It is refused at submit**: HTTP 403, `{"code":40310000,"message":"asset \"OCGN\" is not fractionable"}`. No order was created, so the refusal is an exception from `submit`, not a later `rejected` status. The case tested is a 0.5-share sell of a 1-share holding in a name that was never fractionable. Not observed: a full exit of a fractional holding in a name that *lost* the flag (#395); paper cannot stage that. See "Consequences" for what the refusal means for the spec. | O | `non_fractionable_sell_fractional.json`, `assets.json` |
| 10 | Split, cash dividend, spin-off, cash merger on a held name | **Not observed.** The recording lasted 30 seconds and held nothing overnight. #86's report quotes Alpaca that paper does not simulate dividends. The first paper window's reconciliations (T71 onward) will show each event on a held name through ledger-versus-broker differences. | N | [#86 report](2026-09-25-alpaca-open-and-depth.md) |
| 11 | Does `Asset` carry a CUSIP? | **No.** Neither asset response has a `cusip` field. They carry `id`, `class`, `exchange`, `symbol`, `name`, `status`, `tradable`, `marginable`, `shortable`, `easy_to_borrow`, `fractionable`, `attributes` and the margin fields. `Asset.cusip` from Alpaca is therefore `None` (the spec allows it nullable). | O | `assets.json` |
| 12 | How many current universe members are not fractionable? | **Not answered here.** The recording read two assets. One read-only `GET /v2/assets?status=active` joined to the current universe would answer it. The decision-time `assets` read already sets `whole_share` per name, so no requirement depends on the count. | N | — |
| 13 | Endpoints behind `fills(since)` | **`GET /v2/account/activities/FILL`** with `after`, `direction=asc`, `page_size` (at most 100) and `page_token`, as `AlpacaTradingRaw.list_fill_activities` calls it. The six fills came back as six activities, one per filled order, each with `order_id`, `qty`, `cum_qty`, `leaves_qty`, `price`, `side`, `symbol` and `transaction_time` (UTC, microseconds). The activity `id` starts with a **New York local** timestamp (`20261008111018097::…` for a 15:10:18.097 UTC fill), so a time must never be parsed from it. | O + D | `fill_activities.json`; S4 |
| 14 | Documented request limit vs `alpaca.trading_requests_per_minute` | **200 requests per minute per account** (a 2022 support answer; a staff forum answer says the same for paper and live). The configured 150 sits below it; confirmed. The recording kept no rate-limit headers. | D | S5, S6 |

### Order lifecycle and reconciliation facts

| Fact | Answer | Grade | Evidence |
|---|---|---|---|
| Submit, accept, fill | Every submit response had status `pending_new`. The market orders on KO were `filled` at the first poll, 3 to 4 ms after submit. The OCGN buy and sell polled `new` once and then `filled`. | O | `buy_fractional.json`, `buy_whole.json`, `sell_fractional.json`, `non_fractionable_buy.json`, `flatten.json` |
| Cancel | The resting limit buy read `new`. A cancel request was followed by `canceled`, with `canceled_at` set. `pending_cancel` was not seen. | O | `resting.json`, `resting_cancelled.json` |
| Reject | Every refusal in the recording was an HTTP error at submit (403 or 422). None was an order that later turned `rejected`. | O (N for a `rejected` status) | `sell_above_held.json`, `duplicate_client_order_id.json`, `non_fractionable_sell_fractional.json` |
| Sell above held | HTTP 403, code **40310000**, "insufficient qty available for order", with `available`, `existing_qty` and `held_for_orders` in the body. This is **the same code as the not-fractionable refusal (#9)**, so only the message tells the two apart. | O | `sell_above_held.json` |
| Partial fill | **Not observed.** Each order filled in one activity (`cum_qty` = `qty`, `leaves_qty` 0). A limit order larger than the displayed size, or one in a thin name, would show `partially_filled` and several activities for one order. | N | `fill_activities.json` |
| Expire | **Not observed.** The resting DAY limit was cancelled, not left to expire. A DAY limit left resting past the 16:00 ET close (`expires_at` was `20:00:00Z`) would show `expired` with `expired_at` set. | N | `resting.json` |
| Positions | Only long positions; `qty`, `qty_available`, `avg_entry_price` and `market_value` as decimal strings. `qty` matched the fills to 9 decimals, so `risk.reconcile_quantity_tolerance` = 1e-6 is confirmed with margin to spare. | O | `positions_held.json` |
| Open orders | `[]` after the cancel and the flatten. | O | `open_orders_after.json` |
| Order fields | `position_intent` is `buy_to_open` or `sell_to_close`. A notional order has `qty: null` and `notional` set, and a quantity order the reverse. `filled_qty` is `"0"` until it fills. `expires_at` is the session close for a DAY order. | O | order fixtures |

### The paper account is a margin account

`account_before.json` reports `multiplier` "4", `shorting_enabled` true, `buying_power` 400023.32 (4 × equity) and `regt_buying_power` 200011.66. The paper account is therefore a **margin** account, while the owner chose a taxable **cash** account for live ([#813](https://github.com/josejuarez96/tradepartner/issues/813)). Nothing is changed for this, because the wrapper reads `cash`, never `buying_power`, and refuses any short (ADR 0015 seam 2). Two answers above may still differ live, and the Phase 6 live work should re-check them on the cash account:

- #3: whether `cash` includes unsettled sale proceeds. On a cash account they may be usable but unsettled, with good-faith-violation rules.
- #5: fees, since live sells pay SEC and FINRA TAF fees.

`non_marginable_buying_power` read 99788.52 on the flat account, $217.31 under `cash`. The recording does not explain the gap. No rule reads that field.

## Consequences

- **Config** (`src/tradepartner/config.py`, `tests/test_config.py`):
  - `alpaca.quantity_decimals` = 9 (#1). `test_quantity_decimals_matches_the_recorded_paper_fills` ties it to `fill_activities.json`.
  - `alpaca.client_order_id_max_length` = 128 (#2).
  - `risk.min_order_notional` = 1.0, confirmed (#7, #8).
  - `risk.reconcile_quantity_tolerance` = 1e-6, confirmed (positions row).
  - `risk.reconcile_cash_tolerance` = 0.01, confirmed for this recording (#5). If Alpaca rounds per fill, the gap between the ledger and `cash` can grow by up to half a cent per fill between two reconciliations. At H1's ~100 to 200 orders per rebalance run, that could exceed 0.01. Six fills cannot settle this, and widening the default changes the reconciliation tests' expectations, which are outside this task's files. It is filed for the owner as [#1290](https://github.com/josejuarez96/tradepartner/issues/1290).
- **Cash clause** (#3): `cash` reflects same-session proceeds, so the two-phase funding stands. No dated rewrite to "before `paper start`" is needed, and no issue is opened on T60b's cash read.
- **#395** (#9): the refusal is now recorded. The spec's existing branch applies: a fractional full exit of a name that lost `fractionable` raises at `submit`, the run halts (req 4's empty `submit` allowlist), and the decision stays open. ADR 0010's dated amendment and the spec's dated note record this. Whether to add a different exit for such names, such as selling the whole-share floor and keeping the residue as `dust`, is an owner decision, filed with the cash-tolerance question as [#1290](https://github.com/josejuarez96/tradepartner/issues/1290). Until then the run halts closed and sends no order.
- **T48c** (the adapter): code 40310000 covers both "insufficient qty" and "not fractionable" (lifecycle table), and no time may be parsed from a fill activity's `id` (#13).

## Sources

All Alpaca pages were seen on 2026-09-27 (PR #299's draft).

- S1: [Alpaca, Fractional Trading](https://docs.alpaca.markets/us/docs/fractional-trading), "Supported Order Types". It allows up to 9 decimal places for `qty` and `notional`.
- S2: [Alpaca, Create an Order](https://docs.alpaca.markets/us/reference/postorder), body params. `client_order_id` may be at most 128 characters.
- S3: [Alpaca, Fractional Trading](https://docs.alpaca.markets/us/docs/fractional-trading), opening paragraph: "as little as $1 worth of shares".
- S4: [Alpaca, Retrieve Account Activities of Specific Type](https://docs.alpaca.markets/us/reference/getaccountactivitiesbyactivitytype-1). It is paginated, with `page_size` up to 100 and `page_token`.
- S5: [Alpaca support, API usage limit](https://alpaca.markets/support/usage-limit-api-calls): 200 requests per minute per account (2022).
- S6: [Alpaca forum, staff answer](https://forum.alpaca.markets/t/is-there-a-way-to-increase-the-200-min-api-call-limit-for-the-trading-endpoint/18110/2). The same limit applies to paper and live.

## Disconfirmation

- **Cash.** The answer to #3 would be wrong if the after reading had been taken after settlement. It was read seconds after the last fill, with `balance_asof` 2026-10-07 on both reads, so no settlement intervened.
- **Rounding.** The rounding answer would be wrong if a fee had been netted. Both fee fields read 0.
- **Precision.** The precision answer would be wrong if Alpaca rounded a 9-decimal request it did not accept. The flatten submitted 0.557436865 and filled exactly 0.557436865.
