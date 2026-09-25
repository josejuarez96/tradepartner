# Research Report: Alpaca missing-bar share and paper fills of pre-open market orders

**Brief:** #106  ·  **Date:** 2026-09-25  ·  **Status:** INCOMPLETE (probes built, owner runs pending)  ·  **Agent/model:** team fearsill, claude-opus-5-5, owner-run probes

This is a probe report, not a literature search. It runs the two probes #86 left to the owner, per the protocols in [alpaca-open-and-depth](2026-09-25-alpaca-open-and-depth.md), "Owner probe protocols": Probe 2 (missing-bar share, feeds `ingest.max_missing_share`) and Probe 3 (pre-open paper fills, feeds `execution.fill_price` and the Phase 4 execution ADR). Probe 1 is #101.

## Answer
**Verdict:** INSUFFICIENT until the owner runs complete  ·  **Confidence:** n/a

Pending. Filled in from the runs below.

## Method
Both scripts are on branch `spike/106-probes` (spike code, never merged), with offline tests of the grading logic. Raw payloads are saved outside the repo (`~/tradepartner-probes/106-*`); nothing is written to the store.

### Probe 2: missing-bar share (`scripts/probe_missing_bars.py`)
- **Universe:** option (b) of the protocol, one `get_all_assets(status=active, asset_class=us_equity)` call; kept rows are `tradable`, on `NYSE`, `NASDAQ` or `AMEX` (Alpaca's name for NYSE American). As of today, so names delisted inside the window are absent (the protocol's survivorship limit).
- **Window:** the 20 XNYS sessions ending 5 sessions before the run date, from `calendar.py`. SIP daily bars (`alpaca_raw.daily_bars(..., feed=SIP)`), 200 symbols per call; the feed echo is recorded.
- **Count:** the protocol's E_s / M_s. Never-traded names leave E; probable IPOs are expected from their first bar. Zero-volume placeholder bars (v=0, n=0) count as missing (#104).
- **Live case:** `live <run_dir>` once, at session close + `ingest.settle_delay_minutes` (60) or later, with the query ending 16 minutes before now (free-plan SIP delay).
- **Rule:** candidate = max(2 × max share, live share + 0.005), rounded up to 0.005, never below either observation; tighten only if below 0.05.

### Probe 3: pre-open paper fills (`scripts/probe_open_fills.py`)
- **Orders:** paper only. KO and AAPL, each a $5 notional (F) and a 1-share (W) market DAY buy, optional 1-share OPG control (O) with `--opg`. The script refuses outside 09:00 to 09:15 ET on a full XNYS session, and refuses a client whose base URL is not the paper endpoint. Client order ids are deterministic (`probe106-<date>-<symbol>-<kind>`), so a same-day re-run is rejected as a duplicate instead of doubling an order.
- **Collect** (after 09:46 ET): final order state, SIP daily bar open, SIP trades 09:29:50 to 09:31:00 ET (listing-exchange print with condition O, else Q), SIP quotes in the 5 minutes before submit and the 2 seconds before fill.
- **Metrics:** latency from 09:30:00 ET; gap to the official and bar opens in bps (buys: positive is a cost); median and worst per order type; flags for "not filled by 09:31", "at the official open" and "at the pre-open ask".
- **Sessions:** 5 normal sessions (no early close, no KO or AAPL earnings), 10 F and 10 W fills.

## Probe 2 results
Pending owner run.

## Probe 3 results
Pending owner runs (5 sessions).

## Caveats & gaps
- Probe 2's universe is today's list; the protocol notes it undercounts halts that ended in a delisting. The issue also suggests re-running once real ingest (T16) exists.
- Probe 3 is paper. It shows how the paper stage records fills, not whether live whole-share orders get auction prices. The live test is the owner's decision.

## UNVERIFIED items
- Which SIP condition codes mark the opening print in Alpaca's trade payload for each tape (the protocol cites O and a Q duplicate, S26). The probe falls back from O to Q and records the raw trades so the choice can be checked.

## Sources
Protocols and sources S1 to S30: [2026-09-25-alpaca-open-and-depth.md](2026-09-25-alpaca-open-and-depth.md). Placeholder bars: #104.
