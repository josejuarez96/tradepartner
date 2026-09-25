# Research Report: Alpaca SIP bars for delisted names, and daily-bar depth

**Brief:** #104  ·  **Date:** 2026-09-25  ·  **Status:** COMPLETE for the sample probed; not a completeness census  ·  **Agent/model:** team interlac, claude-opus-5-5, owner-run probe (owner keys, run by the agent with the owner's permission)

This is a probe report, not a literature search. It answers questions left open in [free-data-terms](2026-09-25-free-data-terms.md) ("whether delisted or inactive symbols return bars", "exact first date of Alpaca daily bars"), [price-vendors (G8)](2026-09-25-price-vendors.md) (Alpaca delisted coverage graded "Absent (not documented)") and ADR 0003's "Verify before the Phase 2 plan" list.

## Answer
**Verdict:** SUPPORTED for the sample: the free plan returns SIP daily bars for delisted names, up to their last session  ·  **Confidence:** high for these 8 names; medium as a statement about all delisted names

All 8 delisted names probed (5 acquired, 3 failed) return raw SIP daily bars that end on their last regular session. Daily bars start on 2016-01-04: nothing earlier is returned. Four behaviours matter for ingest and the security master: a zero-volume placeholder bar can follow the last real session; after a ticker change the history is served under **both** symbols; failed names disappear from the assets endpoint while acquired names stay as `inactive`; and nothing here tells us how complete delisted coverage is across the whole market.

## Method
`scripts/probe_delisted_bars.py` on branch `spike/104-delisted-bars` (commit 9bcbaf9; spike code, never merged), run 2026-09-25 07:46 UTC with the owner's keys. About 20 read-only calls through `alpaca_raw.daily_bars(..., feed=SIP)` (raw, unadjusted, no `asof`) and `alpaca_raw.assets_snapshot`. No orders. Raw payloads are kept by the owner outside the repo; the grading is offline and covered by tests on the spike branch.

- Delisted names: bars from 60 days before to 30 days after the expected last session. The expected last sessions come from public reporting, not from filings read here.
- Ticker change: FB and META, 2022-05-02 to 2022-07-29 (META first traded 2022-06-09).
- Depth: SPY and KO, 2015-12-01 to 2016-01-29.

## Evidence

| Symbol | Event | Expected last session | Bars returned | Last bar | Assets endpoint today |
|---|---|---|---|---|---|
| CELG | acquired by BMY | 2019-11-20 | 43 | 2019-11-20 | `inactive`, not tradable |
| MLNX | acquired by NVDA | 2020-04-24 | 44 | 2020-04-24 | `inactive`, not tradable |
| XLNX | acquired by AMD | 2022-02-11 | 43 | 2022-02-11 | `inactive`, not tradable |
| TWTR | taken private | 2022-10-27 | 43 | 2022-10-27 | `inactive`, not tradable |
| SIVB | bank failure (halted 2023-03-10) | 2023-03-09 | 42 | 2023-03-09 | 404 "asset not found for SIVB" |
| FRC | bank failure (seized 2023-05-01) | 2023-04-28 | 44 | 2023-04-28 | 404 "asset not found for FRC" |
| BBBY | bankruptcy, delisted to OTC | 2023-05-02 | 42 | 2023-05-02 | 404 "asset not found for BBBY" |
| ATVI | acquired by MSFT | 2023-10-12 | 44 | 2023-10-13 placeholder, see below | `inactive`, not tradable |

- **Placeholder bar.** ATVI's 2023-10-13 bar has `v` 0, `n` 0 and open = close = the prior close (94.42). The last real trade is 2023-10-12, as expected. No other name in the sample has one.
- **Ticker change.** FB returns 27 bars, 2022-05-02 to 2022-06-08, then nothing. META returns 62 bars from 2022-05-02, and its 27 bars before the rename are identical to FB's (same close and volume on every day). History is copied back onto the new symbol and also kept under the old one. This is without the `asof` parameter, which the API reference says maps symbols across renames (G8 S12).
- **Depth.** SPY and KO each return 19 bars, the first on 2016-01-04. Nothing is returned for December 2015.
- **Last prices** are plausible for each event: SIVB 106.04 (2023-03-09), FRC 3.51, BBBY 0.0751, TWTR 53.70.

## Disconfirmation
- What was found against: the G8 report cites two Alpaca forum threads saying inactive symbols return no bars. This probe contradicts them for SIP daily bars on the free plan today. Those threads may concern the IEX feed, other endpoints, or older API behaviour; they were not re-read here.
- What could still be wrong: 8 names is a sample chosen from well-known events, all delisted 2019 to 2023. Small caps, names delisted in 2016 to 2018, and names whose ticker was later reused by another company were not probed.

## Caveats & gaps
- Coverage completeness (the share of all delistings since 2016 with bars) is not measured. That needs a list of delistings, for example EDGAR Form 25 filings, which is T8/T12 territory.
- Bars stop at the exchange delisting. OTC trading afterwards (SIVBQ, BBBYQ) is not returned under the old symbol, so a delisting return has to come from somewhere else (the spec's delisting-return rule).
- Expected last sessions are from public reporting, not filings. They all agree with Alpaca.

## Consequences for the build (not decisions)
- **Ingest and health (T10, T12, T16, T18):** treat a bar with `v == 0` and `n == 0` as "no trade", not as a price. It would otherwise count as present in the missing-bar share (#106) and could serve as a fill price.
- **Security master (T8):** key securities on identity (CUSIP or CIK), never on ticker. A symbol-keyed ingest that fetches FB and META stores Meta's pre-rename history twice.
- **Universe of delisted names:** the assets endpoint cannot list failed companies (404). Build the list from EDGAR (Form 25) or another source; use the assets endpoint only for currently listed names.
- **ADR 0003 and the Phase 3 vendor ADR:** free Alpaca data includes at least some delisted names from 2016. The G8 grade "Absent (not documented)" is superseded for Alpaca by "Observed, sample of 8".

## UNVERIFIED items
- Last sessions for the 8 names and the FB→META date come from public reporting, not from filings.
- Whether the `asof` parameter changes the ticker-change behaviour (not probed).

## Follow-up questions (not answered here)
- What share of all delistings since 2016 has Alpaca bars? Needs a full delisting list.
- Do names delisted in 2016 to 2018, or tickers later reused by another company, behave the same?

## Sources
- Probe run: `spike/104-delisted-bars` at 9bcbaf9, output `~/tradepartner-probes/104-delisted/20260925T074613Z/` (owner's machine).
- G8 S12: Alpaca, Historical bars API reference, https://docs.alpaca.markets/us/reference/stockbars (for `asof`).
