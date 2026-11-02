# Research Report: do OPG opening-auction orders fill on Alpaca paper, whole shares?

**Brief:** TradePartner T374 (#2851; paper spec amendment #2842, item 4's gate) · **Date:** 2026-11-01 (protocol) · **Status:** PROTOCOL READY, RESULTS PENDING the owner's two keyed runs · **Source:** script `~/tradepartner-probes/sandbox/batch6_opg.py` (outside the repo, written by the agent, run by the owner), logs in `~/tradepartner-probes/sandbox/results/batch6-*.jsonl`

## Answer

Pending. This note is written before the runs so the question, the gate and the recording rules are fixed first. After the second run the owner (or the next agent) fills the Results table below from the two `batch6-*.jsonl` summaries and applies the gate. No result is claimed here.

**Why it matters.** In Probe 3 ([2026-10-09](2026-10-09-probe3-preopen-fills.md)) every paper OPG order expired unfilled. The amendment builds the auction book (T375, T376a, T376) only if paper fills OPG, because without fills nothing about the auction path can be checked on paper.

## The gate (fixed in the plan line)

- **10 OPG orders:** five one-share OPG market orders in each of two sessions, in five names with 20-day median dollar volume over $200M, sent between 08:30 and 09:20 ET.
- **T375 is claimable only if at least 8 of the 10 fill on paper.** Otherwise the PR that records the result edits T375, T376a and T376 to "parked until Phase 6" (item 4's "Built only if paper fills OPG").
- A "fill" is `status = filled` with `filled_qty` 1. A partial fill cannot occur on one share, but the record keeps `filled_qty` and the terminal status regardless.

## Design

Two sessions, one account (the flat `sandbox` account, PA32VP9UD, keys `ALPACA_SANDBOX_API_KEY` / `_SECRET`, paper host only, the probe README's rules and its $500 order cap):

| Session | Gate orders (count toward the 10) | Extras, every session (cannot fill, open no position) |
|---|---|---|
| `buy` | five OPG market buys, 1 share, `buy_to_open` | cancel of OPG limit A at cutoff - 60 s (09:27:00); cancel of OPG limit B at cutoff + 30 s (09:28:30); one OPG limit sent at cutoff + 45 s (the refusal); one CLS limit for the record |
| `sell` | five OPG market sells, 1 share, `sell_to_close`, of the five names the buy session bought | the same four extras |

The extras are limit orders at 50% of the last price on a sixth name, so they cannot fill whatever the cancel does. The second session sells what the first bought, so the account ends flat. CLS is recorded only (item 4: the closing auction is specified, not built).

**Recorded per gate order:** status lifecycle, `filled_qty`, `filled_avg_price`, `filled_at`, `expired_at`, `canceled_at`, any partial and its terminal status; the fill price against the opening-auction print (Alpaca's auctions endpoint, SIP feed, best effort; if it is unavailable, the owner reads the listing exchange's official opening print by hand and writes it in the table). **Recorded per extra:** the HTTP status and body of each cancel (time sent versus the cutoff, status afterwards), the late OPG's refusal code and message (item 4 expects code `40310000`, the same as insufficient buying power, told apart by message), and the CLS order's acceptance or refusal.

## Guards in the script

Dry run unless `--submit`; the plan (every order body and the timed schedule) is printed before anything is sent; refuses unless the account number is PA32VP9UD, the account is flat with no open orders (buy session) or holds only one share of the probe names (sell session), the market is closed with next open today, and the broker-corrected ET time is inside 08:30-09:20; credentials only from the two sandbox variables, refused when equal to any paper-book key; trading host is `paper-api.alpaca.markets`, data host GET only; no notional, fractional or extended-hours field on any order; every order has a `sbx-batch6-` client order id; leftover open orders are cancelled at the end. Offline test: `uv run --project ~/Projects/tradepartner python ~/tradepartner-probes/sandbox/selftest_opg.py` (a fake broker, no network).

## Owner's run steps

Needs: the sandbox keys in the environment (the README's `env $(grep ...)` form from the main checkout), the account flat, and two session mornings (they need not be consecutive; the account stays long five shares in between).

1. **Dry run (any time; sends nothing).** `cd ~/Projects/tradepartner && env $(grep -E '^ALPACA_SANDBOX_API_(KEY|SECRET)=' .env | xargs) uv run --project ~/Projects/tradepartner python ~/tradepartner-probes/sandbox/batch6_opg.py --session buy`. Read the printed PLAN. Check the five names are over $200M 20-day median dollar volume (the script checks it best effort and prints `UNVERIFIED` if the data feed refuses); change them with `--symbols A,B,C,D,E` if not.
2. **Session 1, buy.** Before 08:30 ET confirm the sandbox account is flat in the Alpaca paper dashboard. Between 08:30 and 09:20 ET run the same command with `--submit` added. It sends the five buys, the extras, then holds until about 09:45 ET (cancels at 09:27:00 and 09:28:30, the late order at 09:28:45, the open at 09:30, polling to 09:45). Keep the terminal open. Save the printed summary and the jsonl path.
3. **Session 2, sell.** On a later session morning, 08:30-09:20 ET: `... batch6_opg.py --session sell --submit`. It refuses unless the account holds one share of each probe name and nothing else.
4. **Check the end state.** The sandbox account is flat and has no open orders (dashboard). A failed buy session may leave fewer than five positions; the sell session sells what is held and counts the rest as unfilled buys.
5. **Hand back** the two summaries (or the jsonl paths). The agent fills the Results below and applies the gate in the PR that records the result.

Nothing here reads or writes the store, so no store copy is needed for this probe; the account is the only shared state, and the script never touches another book's keys.

## Results

| Session | Name | Side | Status | Filled qty | Fill price | Opening print | Gap (bps) | Terminal time |
|---|---|---|---|---|---|---|---|---|
| buy | | | | | | | | |
| sell | | | | | | | | |

| Extra | Sent (ET) | Response | Notes |
|---|---|---|---|
| Cancel A, cutoff - 60 s | | | |
| Cancel B, cutoff + 30 s | | | |
| Late OPG, cutoff + 45 s | | | refusal code and message |
| CLS limit | | | |

**Fills: _ of 10. Gate: _ (T375 claimable / T375, T376a, T376 parked until Phase 6).**

## Caveats

- Two sessions are two draws. A fill count of 8 or 9 on one-share market orders in liquid names would still say little about thin names or large orders; the gate asks only whether paper fills OPG at all.
- Paper fills are simulated by Alpaca, not matched in the real auction; the opening-print gap measures how closely the simulation follows the print, not real auction execution.
- The cancel and late-order timings depend on the broker-corrected clock the script uses; the log records both the script's time and the broker's response.
