# Research Report: Alpaca paper fills of pre-open market orders (Probe 3)

**Brief:** #182 (split from #106)  ·  **Date:** 2026-10-09  ·  **Status:** COMPLETE for the two sessions the owner chose (the protocol asked for five; see Caveats)  ·  **Agent/model:** team timingkeys, claude-fable-5-1; sessions run on the owner's paper account (2026-10-05 by the owner, 2026-10-09 by orchestrator bd55db6f with the owner's approval)

This is a probe report, not a literature search. It runs Probe 3 of the protocols in [alpaca-open-and-depth](2026-09-25-alpaca-open-and-depth.md), "Owner probe protocols": how Alpaca's **paper** simulator fills market DAY orders queued before the open. It feeds the T70 amendment to ADRs 0005 and 0006 (#294: `paper.tracking_rule` and the six `paper.*` timing keys of #247 Q14), `execution.fill_price` and the Phase 4 execution ADR. Probe 1 is [#101](2026-09-25-alpaca-open-and-depth.md#probe-1-results-owner-run-2026-09-25-101); Probe 2 is [#106](2026-09-25-alpaca-missing-bars.md).

## Answer
**Verdict:** SUPPORTED for the direction of every finding below (4 of 4 orders of each kind behaved the same way on both sessions and both names); INSUFFICIENT for the magnitudes as estimates of a distribution (four fills per order type, two sessions, paper only)  ·  **Confidence:** medium for the direction; low for the tails

On paper, a **$5 notional fractional buy (F)** queued at 09:01-09:05 ET filled at the **official opening print, to the cent, in all four cases**, 0.9 to 2.0 s after 09:30:00 ET (0.1 to 0.4 s after the listing exchange's opening print). A **1-share whole-share buy (W)** queued at the same time filled at the **NBBO ask at fill time, to the cent, in all four cases**, but late: 17 to 118 s after the open (median 53 s), 5.7 to 34.7 bps above the official open in three cases and 9.3 bps below it in one (median +6.2 bps, worst +34.7 bps). A **1-share OPG order (O) expired without filling in all four cases**, 53 to 168 s after the open: paper no longer simulates OPG as a market order, contrary to the 2024 staff statement (S26). No F order filled at the pre-open NBBO. The protocol's reading table (its row 1, "first NBBO after open", and row 4, "does not fill by 09:31") describes W; it has no row for F's outcome, which is better than row 1 expected (no half-spread), and none for O's. What this changes for the build is in "What the reading table says".

## Method
The script is `scripts/probe_open_fills.py` on branch `spike/106-probes` (commit 1a7c749; spike code, never merged; safety-reviewer PASS WITH FIXES on PR #112, fixes applied). It refuses any client whose base URL is not the paper endpoint, refuses `submit` outside 09:00-09:15 ET on a full XNYS session, and uses deterministic client order ids (`probe106-<date>-<symbol>-<kind>`), so a re-run the same day is rejected by Alpaca as a duplicate. Nothing is written to the store. The raw payloads are in `~/tradepartner-probes/106-open/<session>/` on the owner's machine, outside the repo; the script's `analyze` output is `~/tradepartner-probes/106-open/report.md`. This report copies no account id, key or broker order id.

**Orders**, per session and per name (KO, NYSE-listed; AAPL, Nasdaq-listed), all market, buy, `extended_hours=false`, placed between 09:00 and 09:15 ET from the owner's paper account with the owner's paper keys:
- **F:** `notional=5`, `time_in_force=DAY` (pure fractional; both names trade far above $5).
- **W:** `qty=1`, `time_in_force=DAY`.
- **O:** `qty=1`, `time_in_force=OPG` (the control; the protocol's optional order, included on both sessions).

**Sessions** (the owner chose two, not the protocol's five): Monday 2026-10-05 (`submit --opg` at 09:01:20 ET, `collect` at 09:54 ET) and Friday 2026-10-09 (`submit --opg` at 09:05:06 ET, `collect` at 09:50 ET). Both are full XNYS sessions (the script's guard). Neither name reported earnings on either date to the author's knowledge; this was not checked against a filing. The probe's positions were flattened afterwards so the account is flat before T71.

**Collected**, after 09:46 ET so the free plan's 15-minute SIP delay is respected: each order's final state (`status`, `submitted_at`, `filled_at`, `filled_qty`, `filled_avg_price`); the SIP daily bar's `o` (the bar open); SIP trades 09:29:50-09:31:00 ET, from which the **official open** is the listing exchange's print with condition `O` and its condition `Q` duplicate (NYSE `N` for KO, Nasdaq `Q` for AAPL; the two prints agree in every case); and SIP quotes around each order's submit and fill times, for the pre-open NBBO and the NBBO at fill.

**Metrics** (protocol): latency = `filled_at` − 09:30:00 ET; gap_official_bps = 10,000 × (fill − official open) / official open; gap_bar_bps = 10,000 × (fill − bar open) / bar open. For buys a positive gap is a cost. "Late" is the protocol's "does not fill by 09:31" (60 s after the open).

## Evidence

**Reference prices per session** (SIP; the NBBO is bid / ask at the latest quote before the time named):

| Session | Name | Official open (listing-exchange `O` print, time ET) | Bar open `o` | Bar vs official | NBBO at submit (ET) | NBBO at F's 09:23 ET release (see finding 4) |
|---|---|---|---|---|---|---|
| 2026-10-05 | KO | 85.70 (09:30:01.428) | 85.87 | +19.8 bps | 86.02 / 86.05 (09:01:12) | 86.11 / 86.17 (09:22:48) |
| 2026-10-05 | AAPL | 332.96 (09:30:01.599) | 332.815 | −4.4 bps | 331.93 / 332.00 (09:01:07) | 332.10 / 332.20 (09:22:57) |
| 2026-10-09 | KO | 87.51 (09:30:01.224) | 87.60 | +10.3 bps | 87.33 / 87.50 (09:05:06) | 87.36 / 87.48 (09:21:57) |
| 2026-10-09 | AAPL | 331.35 (09:30:00.857) | 331.695 | +10.4 bps | 331.25 / 331.40 (09:05:06) | 331.51 / 331.75 (09:23:01) |

**Per order** (fill times in ET; "NBBO at fill" is the latest SIP quote before `filled_at`):

| Session | Order | Status | Filled at | Latency s | Fill | NBBO at fill | vs official bps | vs bar bps | By 09:31? | = official? | = pre-open ask? |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-10-05 | probe106-20261005-KO-F | filled | 09:30:01.841 | 1.84 | 85.70 (0.058226371 sh) | 85.65 / 85.70 | 0.00 | −19.80 | yes | yes | no |
| 2026-10-05 | probe106-20261005-KO-W | filled | 09:31:13.919 | 73.92 | 85.62 (1 sh) | 85.58 / 85.62 | −9.33 | −29.11 | **no** | no | no |
| 2026-10-05 | probe106-20261005-KO-O | **expired** 09:32:48 | | | | | | | no | | |
| 2026-10-05 | probe106-20261005-AAPL-F | filled | 09:30:02.042 | 2.04 | 332.96 (0.014986785 sh) | 332.68 / 332.94 | 0.00 | +4.36 | yes | yes | no |
| 2026-10-05 | probe106-20261005-AAPL-W | filled | 09:31:58.472 | 118.47 | 333.18 (1 sh) | 333.07 / 333.18 | +6.61 | +10.97 | **no** | no | no |
| 2026-10-05 | probe106-20261005-AAPL-O | **expired** 09:31:34 | | | | | | | no | | |
| 2026-10-09 | probe106-20261009-KO-F | filled | 09:30:01.491 | 1.49 | 87.51 (0.057022054 sh) | 87.39 / 87.53 | 0.00 | −10.27 | yes | yes | no |
| 2026-10-09 | probe106-20261009-KO-W | filled | 09:30:17.178 | 17.18 | 87.56 (1 sh) | 87.51 / 87.56 | +5.71 | −4.57 | yes | no | no |
| 2026-10-09 | probe106-20261009-KO-O | **expired** 09:30:53 | | | | | | | no | | |
| 2026-10-09 | probe106-20261009-AAPL-F | filled | 09:30:00.935 | 0.93 | 331.35 (0.015059604 sh) | 331.71 / 331.77 | 0.00 | −10.40 | yes | yes | no |
| 2026-10-09 | probe106-20261009-AAPL-W | filled | 09:30:31.580 | 31.58 | 332.50 (1 sh) | 332.44 / 332.50 | +34.71 | +24.27 | yes | no | no |
| 2026-10-09 | probe106-20261009-AAPL-O | **expired** 09:31:52 | | | | | | | no | | |

**Summary per order type** (median / worst; four orders each; "worst" is the largest cost for a buy):

| Type | Fills | Latency s | vs official bps | vs bar bps | Priced at |
|---|---|---|---|---|---|
| F ($5 notional, DAY) | 4 of 4 | 1.67 / 2.04 | 0.00 / 0.00 | −10.3 / +4.4 (range −19.8 to +4.4) | the official opening print, 4 of 4 |
| W (1 share, DAY) | 4 of 4 | 52.8 / 118.5 | +6.2 / +34.7 (range −9.3 to +34.7) | +3.2 / +24.3 (range −29.1 to +24.3) | the NBBO ask at fill time, 4 of 4 |
| O (1 share, OPG) | 0 of 4 | expired 53 to 168 s after the open | | | never filled |

**Findings**
1. **F fills at the official open, not at the NBBO.** All four F fills equal the listing exchange's opening print to the cent, 0.08 to 0.44 s after that print. The NBBO ask at fill differed from the fill in three of the four (332.94 vs 332.96; 87.53 vs 87.51; 331.77 vs 331.35, that last 42 cents above the fill), and the last SIP trade before each fill was not the opening print either (85.68 and 85.69 vs 85.70; 333.075 vs 332.96; 87.46 vs 87.51; 331.75 and 331.76 vs 331.35). So paper priced the released fractional order at the listing exchange's opening print itself, not at the last trade and not at the NBBO, even after later prints had moved up to 41 cents away from it. F's latency is the auction print's own delay after 09:30:00 (0.9 to 1.6 s here) plus under half a second.
2. **W fills at the NBBO ask, 17 to 118 s after the open.** All four W fills equal the ask of the latest SIP quote before `filled_at`, to the cent. The release delay varied from 17 s to 118 s within the same session for the two names (10-05: 74 s and 118 s; 10-09: 17 s and 32 s). The gap to the official open is the price drift over that delay: +5.7, +6.6 and +34.7 bps as costs, −9.3 bps as a gain. Two of the four were not filled by 09:31, the protocol's "late" line.
3. **OPG does not fill on paper.** All four O orders expired 53 to 168 s after the open with `filled_qty = 0`. The 2024 staff statement that paper simulates OPG as a market order (S26) no longer describes the simulator. OPG is not a usable order type for the paper stage.
4. **Alpaca restamped the queued fractional orders' `submitted_at` to 09:23:01 ET on both sessions** (created 09:01:20 ET and 09:05:06 ET), while W and O kept a `submitted_at` within 15 ms of creation. The simulator appears to release queued fractional orders at a fixed pre-open time, then price them at the first trade after 09:30. For the journal this confirms that the fill cursor must rest on the run's own `known_at` of the `pending` event (spec req 8), never on the broker's `submitted_at`.
5. **No F order filled at the pre-open NBBO.** The asks at submit (86.05, 332.00, 87.50, 331.40) and at the 09:23 release (86.17, 332.20, 87.48, 331.75) differ from every F fill. The "at the time the order was submitted" reading of S21 (Caveats of the #86 report) does not apply on paper.
6. **Every order was acknowledged within milliseconds.** Each order's broker `created_at` is 25 to 213 ms after the script's local submit stamp (two clocks, so this includes their offset): `accepted` for the four F orders, `pending_new` for the W and O orders, whose final records carry a `submitted_at` 5 to 14 ms after `created_at` (the acknowledgement; no read was taken between submit and `collect`). This matches the T48b recording ([alpaca-paper-facts](2026-10-08-alpaca-paper-facts.md): market orders filled at the first poll, 3 to 4 ms after submit, during hours).
7. **The SIP bar open is not the official open** (FDT S20 confirmed on four more cases): the bar's `o` sat 4.4 bps below to 19.8 bps above the opening print. Any `open` fill convention in a backtest would be measured against the bar open, which is the first eligible SIP trade, not the auction.

## What the reading table says
The protocol's table maps paper outcomes to what they change:
- **W is row 1 plus row 4.** It filled at the NBBO ask after the open (row 1: "paper's model is first NBBO after open"), but the release delay was 17 to 118 s, not a few hundred milliseconds, and two of four missed 09:31 (row 4: "a paper queue or release delay; record it; it affects the pre-open rebalance timing assumption"). Row 1's consequence stands: the paper stage records whole-share fills at the ask some seconds to two minutes after the open, and a backtest with `execution.fill_price = "open"` would need a slippage allowance at least the observed p90 of |gap_bar_bps| plus a half-spread; on these four fills |gap_bar_bps| ranged 4.6 to 29.1. That allowance key does not exist, and creating it stays with the execution ADR; H1's frozen `close` convention is not touched.
- **F has no row.** It filled at the official open, which row 1 said it would not, and not at the pre-open NBBO, which row 2 asked about. The nearest consequence is row 3's caution: this is the simulator's choice of reference price and must not be generalised to live, where the Customer Agreement prices a fractional fill between the NBBO (S22 §28). For the paper stage, a pre-open notional buy records the opening print as its fill. The run's buys are submitted after the sells, during hours, so their fills follow T48b's during-hours case (the then-current NBBO within milliseconds), not F's.
- **O has no row either.** Row 3 ("W or O fills exactly at the official open on paper") did not happen; the opposite did. Nothing in the build uses OPG, and ADR 0006's "two phases at the NBBO after the open" needs no change on this account.
- **Row 3's "re-open this report" action.** In substance F's fill at the official open and O's expiry both contradict S26 as the #86 report read it. That report is frozen (docs/README.md: newer research supersedes, it is not edited), so it is not re-opened; this report supersedes its "Paper vs live" paragraph and evidence row 3c for pre-open fractional orders and for OPG on paper, and the claims register carries the conflict (P3-1 and P3-3 against AO-3, each naming the other).
- **The pre-open rebalance timing assumption** (ADR 0006, spec req 3, the `paper.*` timing keys): every probed order was a buy; **sells were not probed**, so the buy-side W delay (17 to 118 s, the largest of four, not a bound) is the only proxy for how long a sell queued before the open takes to become terminal on paper. On that proxy the buys phase can begin within minutes of the open. The T70 amendment (#294) sets `paper.sell_wait_seconds` and the other timing keys from these numbers and says plainly that they rest on four buy fills from two sessions.
- **The fill-timing term** (spec req 10, the `residual` rule): the whole-share fills sat −9 to +35 bps from the official open, which is the paper-side drift the term adds back; the dividend and residue terms are unaffected by this probe.

## Disconfirmation
- **Could F's fill equal the opening print by coincidence?** Four of four, across two names on two sessions, to the cent, each 0.1 to 0.4 s after the print, and three of the four unequal to the contemporaneous ask: coincidence is implausible. The last trade before each fill was a different price (finding 1), which rules out last-trade pricing; the mechanism (the simulator pricing a queued fractional order at the official open) is inferred, not documented.
- **Could W's delay be the probe's own polling?** No: `filled_at` is the broker's stamp, read at `collect` after 09:46, and the fills equal the ask at that stamp, not at any read time.
- **Could O have expired because OPG was submitted too late?** No: OPG submitted after 09:28 ET is rejected (S25b); these were submitted at 09:01 and 09:05 ET, returned `pending_new` with an acknowledgement stamp milliseconds later, and reached `expired` only after the open (53 to 168 s after 09:30:00).
- **Evidence that any of this holds live:** none. Paper omits price improvement, latency slippage and the auction (S27, S26); live fractional fills are principal fills between the NBBO (S22 §28), and whether live whole-share DAY orders reach the auction is still **not documented** (#86 report). A small live test remains the owner's decision.

## Caveats & gaps
- **Two sessions, not five.** The owner chose two (comment on #182, 2026-10-08). Four fills per order type cannot bound a tail: the worst W latency (118 s) and gap (+34.7 bps) are the largest of four, not a p90 or p99. The direction of every finding was the same in all four cases; the magnitudes are indicative.
- **Paper, not live.** Every number describes Alpaca's simulator, which is what the paper stage will record; none transfers to a live account (see Disconfirmation).
- **Two liquid large-cap names** at ordinary open volumes (opening prints of 150,774 to 483,688 shares). A thin name, an early close or a volatile open may behave differently; the H1 universe reaches well below these names' liquidity.
- **Only buys were probed.** The protocol's three orders are all buys. The run's sells phase, whose fill time `paper.sell_wait_seconds` bounds, is inferred from the W buys' release delay; a sell may be released on a different schedule.
- **Only pure shapes were probed.** The run's sells are by quantity and can mix whole shares and a fraction (phases.py); a mixed pre-open order was not probed, and F and W behaved differently. The first paper window's own fills will show which case applies.
- **Buys submitted during hours were not probed here**; T48b's recording covers that case (fills at the first poll).
- Earnings dates were not checked against filings; the protocol's "not earnings days" rule was judged from the calendar habit of both names (late October).

## UNVERIFIED items
- The mechanism behind finding 1 (a queued fractional order priced at the official opening print on release) and finding 4 (a fixed 09:23 ET release of queued fractional orders): inferred from timestamps on two sessions, not documented by Alpaca.
- Whether paper's W release delay depends on the name, the session's opening volume or the queue position; two names on two sessions cannot separate these.
- Everything live (auction participation of whole-share DAY orders; which NBBO prices a live fractional fill), as in the #86 report.

## Follow-up questions (not answered here)
- The T70 amendment (#294): the timing keys and `paper.tracking_rule = residual`, set from this report.
- Whether to run the small live test the protocol describes (5 sessions × 1 share plus $5 notional): the owner's decision; `execution.fill_price` stays `close` until then.
- A re-measurement from the first paper window's own fills (mixed-quantity sells, notional buys during hours, many names) once T71 runs: that is the five-session sample this probe lacks, at no extra cost.

## Sources
- Protocol and sources S21 to S30 (Alpaca documents and staff forum answers): [2026-09-25-alpaca-open-and-depth.md](2026-09-25-alpaca-open-and-depth.md), "Owner probe protocols", Probe 3. FDT S1 to S20: [2026-09-25-free-data-terms.md](2026-09-25-free-data-terms.md).
- The T48b paper recording (acknowledgement and during-hours fill timing): [2026-10-08-alpaca-paper-facts.md](2026-10-08-alpaca-paper-facts.md).
- Script: `scripts/probe_open_fills.py` on `spike/106-probes` (commit 1a7c749), never merged.
- Raw payloads and the generated `report.md`: `~/tradepartner-probes/106-open/2026-10-05/` and `2026-10-09/` (owner's machine, not in git).
