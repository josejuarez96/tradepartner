# Research Report: Alpaca missing-bar share on SIP daily bars

**Brief:** #106  ·  **Date:** 2026-09-25  ·  **Status:** INCOMPLETE (history and next-day check done; the same-day live case is pending)  ·  **Agent/model:** teams fearsill and eclipse, claude-opus-5-5, owner-run probe (owner keys)

This is a probe report, not a literature search. It runs Probe 2 of the protocols in [alpaca-open-and-depth](2026-09-25-alpaca-open-and-depth.md), "Owner probe protocols": the missing-bar share, which feeds `ingest.max_missing_share` (currently 0.05). Probe 1 is #101. Probe 3 (pre-open paper fills) was split to #182 and gets its own report.

## Answer
**Verdict:** SUPPORTED: the source delivers a row for almost every listed name every session, so 0.05 is far looser than needed for a staleness test  ·  **Confidence:** high for delivery on historical sessions; medium for ingest time until the live case runs

Across 20 sessions and 8,814 listed names, SIP daily bars were absent for at most 0.03% of expected names on any session (median 0.01%). A name with no trades still gets a zero-volume placeholder row, not a gap. Read as "rows absent" (reading A below), the tightening rule gives **0.010**. Read as "no traded bar" (#104's rule for delistings), about 7% of names are missing on a normal day, which would flag every session as stale at 0.05. The owner sets the value; this report recommends reading A and 0.010, pending the live case.

## Method
The script is `scripts/probe_missing_bars.py` on branch `spike/106-probes` (commit 1a7c749; spike code, never merged, 40 offline tests). The raw payloads are in `~/tradepartner-probes/106-missing/20260925T080601Z`, outside the repo. Nothing is written to the store.

- **Universe:** option (b) of the protocol. One `get_all_assets(status=active, asset_class=us_equity)` call; kept rows are `tradable` and on `NYSE`, `NASDAQ` or `AMEX` (Alpaca's name for NYSE American). The list is as of the run date.
- **Window:** the 20 XNYS sessions ending 5 sessions before the run date, from `calendar.py`: 2026-08-21 to 2026-09-18. SIP daily bars (`alpaca_raw.daily_bars(..., feed=SIP)`), 200 symbols per call.
- **Count:** the protocol's E_s / M_s. A name is expected from its first row of any kind in the window (placeholders included). A name with no row in the window is "never traded" and leaves E. A name whose first row is after the window start is a probable IPO.
- **Rule:** candidate = max(2 × max share, live share + 0.005), rounded up to 0.005, never below either observation. Tighten only if the candidate is below 0.05.

**Runs**
1. **History:** `fetch`, 2026-09-25 08:06 UTC, team fearsill with the owner's keys. 45 read-only calls, 0 errors, feed echo `sip`.
2. **Next-day check:** `live`, 2026-09-25 19:47 UTC (15:47 ET), run by the owner. The run came before today's close, so the script read the last completed session, 2026-09-24, about 23 hours after its ingest time (close + `ingest.settle_delay_minutes`). The script printed a warning. It shows how complete a session looks a day later. It is **not** the same-day live case the protocol asks for. 45 calls, 0 errors.
3. **Live case (pending):** `live` between 17:00 and 19:00 ET on a normal session, so the latest session is read about an hour after the close.

## Evidence

**Universe:** 8,814 names. By asset name, 7,304 are common-like; the rest are 438 warrants, 421 preferreds, 345 units, 176 notes and 130 rights. In the window, 30 names had no row at all ("never traded", 22 of them common-like) and 75 were probable IPOs (50 common-like).

There are three ways to read "missing":

| Reading | E, last session | Median share | Max share (session) | Next-day check 2026-09-24 | Rule's candidate |
|---|---|---|---|---|---|
| A. No bar row (placeholders count as present) | 8,784 | 0.0001 | 0.0003 (2026-08-31) | 1 of 8,784 (0.0001), IPXGU | **0.010**, tighten |
| B. No traded bar (placeholders count as missing) | 8,784 | 0.0704 | 0.0796 (2026-09-11) | 628 of 8,784 (0.0715) | 0.160: keep 0.05, investigate |
| C. As B, common-like names only | 7,282 | 0.0183 | 0.0222 (2026-09-09) | 131 of 7,282 (0.0180) | 0.045, tighten |

The candidates in the last column use the next-day check in place of the live share. The live case can only raise them.

**Findings**
- **The source nearly always delivers a row.** The window held 12,398 zero-volume placeholder rows (v=0, n=0) across 1,286 names; about three quarters were on units, warrants and rights. Only 19 name-sessions had no row at all, all on three SPAC tickers: IPXGU (17 sessions), ATLQU (1) and ATLQW (1). IPXGU was also the only absent name in the next-day check.
- **Reading A is the staleness signal.** It asks whether the source has delivered the session, which is the question spec rule 10 asks. Its candidate is 0.010, five times tighter than today's 0.05. The arithmetic: 2 × 0.0003 = 0.0006 and 0.0001 + 0.005 = 0.0051; the larger rounds up to 0.010.
- **Reading B cannot be the staleness test on this universe.** About 7% of listed names do not trade on a normal day, so 0.05 would flag every session. #104's rule (placeholder = missing) is right for a delisting check, where a placeholder after the last session means the name is gone. It is wrong for the market-wide share.
- **Reading C mixes illiquidity with delivery.** Eight common-like names had no trade in all 20 sessions (BIO.B, EFTY, HCHL, LAWR, MAGH, MAMK, PC, UCFI). That is a universe-filter question (T13's liquidity rule), not a staleness one.
- **The IPO rule in the protocol needed a fix.** Read as "first *traded* bar after window start", it flagged 585 names, because any name idle on 2026-08-21 counted as an IPO, and the share then climbed through the window. Taking a name as expected from its first row of any kind gives 75 probable IPOs.

**Recommendation for the owner**
- Measure staleness as reading A: rows absent from the listed set.
- Set `ingest.max_missing_share` to 0.010, unless the live case comes in above 0.005. In that case, use the rule's candidate from the live share.
- Spec rule 10 should say that a zero-volume row counts as present for staleness, and T16's stale check should be confirmed to match. That is a spec note for T16 and is not changed here.

## Disconfirmation
- **Could placeholders hide undelivered sessions?** If Alpaca emitted placeholders for a session it had not yet built, reading A would miss staleness. The next-day check argues against it: the no-trade share on 2026-09-24 (0.0715 in reading B) sits inside the historical range (0.0634 to 0.0796). An undelivered session would show a jump in placeholders. The same-day live case is the direct test.
- **Could the history look more complete than ingest time?** Yes, by design of the protocol: bars are corrected after the session. The next-day check (about 23 hours later) matches the history. The live case, about an hour after the close, is still pending.
- **Survivorship:** the universe is today's list, so names delisted inside the window are absent. That undercounts halts that ended in a delisting, as the protocol notes.

## Caveats & gaps
- **Live case pending:** until it runs, the 0.010 candidate rests on historical and next-day data.
- **One window:** 20 sessions in late summer 2026. No early-close day, index rebalance or market-wide halt is in it.
- **Re-run with real ingest:** the issue suggests measuring again once real ingest (T16 with the real EDGAR `FilingSource`) runs, with the store's own listed set instead of Alpaca's asset list.

## UNVERIFIED items
- Whether Alpaca ever emits a placeholder for a session its SIP feed has not finished. It is not documented; the disconfirmation above is indirect.

## Follow-up questions (not answered here)
- Pre-open paper fills (Probe 3): #182.
- A spec note for T16: a zero-volume row counts as present for the staleness share.

## Sources
- Protocols and sources S1 to S30: [2026-09-25-alpaca-open-and-depth.md](2026-09-25-alpaca-open-and-depth.md).
- Placeholder bars after a delisting: [2026-09-25-alpaca-delisted-bars.md](2026-09-25-alpaca-delisted-bars.md) (#104).
- Probe payloads and generated report: `~/tradepartner-probes/106-missing/20260925T080601Z/` (owner's machine, not in git).
