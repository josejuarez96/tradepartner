# Research Report: Alpaca missing-bar share and paper fills of pre-open market orders

**Brief:** #106  ·  **Date:** 2026-09-25  ·  **Status:** INCOMPLETE (Probe 2 historical run done; live case and Probe 3 pending)  ·  **Agent/model:** team fearsill, claude-opus-5-5, owner-run probes

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
Run 1 by team fearsill with the owner's keys and permission, 2026-09-25 08:06 UTC: `probe_missing_bars.py fetch` on `spike/106-probes`. It made 45 read-only calls with 0 errors, and the feed echo was `sip`. The run was re-graded offline at commit 1a7c749. The live case is pending: it runs after 17:00 ET today.

- **Universe:** 8,814 active, tradable names on NYSE, Nasdaq and NYSE American as of today. By asset name, 7,304 are common-like. The rest are 438 warrants, 421 preferreds, 345 units, 176 notes and 130 rights.
- **Window:** 2026-08-21 to 2026-09-18, 20 XNYS sessions.
- **Finding 1: the source nearly always delivers a row.** A name with no trades on a session still gets a zero-volume placeholder bar (v=0, n=0), not a gap. The run held 12,398 such rows across 1,286 names; about three quarters were units, warrants and rights. Only 19 name-sessions had no row at all, all on three SPAC tickers (IPXGU 17, ATLQU 1, ATLQW 1).
- **Finding 2: the protocol's IPO rule needed a fix.** The protocol treats a name as a probable IPO when its first bar comes after the window start. Taken as "first *traded* bar", that flagged 585 names, because any name that did not trade on 2026-08-21 counted. That also made the share climb through the window. The probe now takes a name as expected from its first row of any kind, which gives 75 probable IPOs.

The rule is "missing if it has no bar", and there are three ways to read it:

| Reading | Names in E (last session) | Median share | Max share (session) | Rule's candidate, provisional |
|---|---|---|---|---|
| A. No bar row (placeholders count as present) | 8,784 | 0.0001 | 0.0003 (2026-08-31) | 0.005 |
| B. No traded bar (placeholders count as missing, #104's rule) | 8,784 | 0.0704 | 0.0796 (2026-09-11) | 0.160 → keep 0.05 |
| C. As B, common-like names only | 7,282 | 0.0183 | 0.0222 (2026-09-09) | 0.045 |

What this means for `ingest.max_missing_share` (the owner sets the value, after the live case):
- **Reading A is the staleness signal.** It asks whether the source has delivered the session, which is the question spec rule 10 asks. The rule gives 0.005, a tenfold tightening. The live case decides whether that holds at ingest time, when the latest session may be less complete.
- **Reading B cannot be the staleness test on this universe.** About 7% of listed names do not trade on a normal day, so 0.05 would flag every session as stale. #104's rule ("count placeholders as missing") is right for a delisting check, where a placeholder after the last session means the name is gone. It is wrong for the market-wide share.
- **Reading C** mixes illiquidity with delivery. Eight common-like names had no trade in all 20 sessions (BIO.B, EFTY, HCHL, LAWR, MAGH, MAMK, PC, UCFI). That is a universe-filter question (T13's liquidity rule), not a staleness one.
- **Recommendation for the owner, pending the live case:** measure staleness as reading A, rows absent from the listed set. Tighten to reading A's candidate if the live case confirms it. The spec wording "listed names are missing it" should say that a zero-volume row counts as present for staleness. That is a spec note for T16 and is not changed here.

## Probe 3 results
Pending owner runs (5 sessions).

## Caveats & gaps
- Probe 2's universe is today's list; the protocol notes it undercounts halts that ended in a delisting. The issue also suggests re-running once real ingest (T16) exists.
- Probe 3 is paper. It shows how the paper stage records fills, not whether live whole-share orders get auction prices. The live test is the owner's decision.

## UNVERIFIED items
- Which SIP condition codes mark the opening print in Alpaca's trade payload for each tape (the protocol cites O and a Q duplicate, S26). The probe falls back from O to Q and records the raw trades so the choice can be checked.

## Sources
Protocols and sources S1 to S30: [2026-09-25-alpaca-open-and-depth.md](2026-09-25-alpaca-open-and-depth.md). Placeholder bars: #104.
