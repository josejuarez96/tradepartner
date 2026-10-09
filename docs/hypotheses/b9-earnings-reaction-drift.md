# Hypothesis: B9, long-only earnings-reaction (EAR) drift, top quintile, 20-session hold, daily

**Family:** `earnings_drift` (proposed root family; not in `hypotheses.families` yet, see "The family question")  ·  **Author:** team hypfiles (agent draft on Fable 5.1, #1358); owner approval to draft 2026-10-09 (#1353 shortlist, #1352 direction)  ·  **Date:** 2026-10-09  ·  **Status:** draft, not registrable: it needs the 8-K `items` field in the store (data-foundation plan task T151), a backtest spec amendment for the family and its engine tasks (listed in #1358's PR body; nothing is built), and the owner's answers to B9-1 to B9-7 below

Merging this file does not register it. Three things stand between this draft and a
registration, in order: (1) the data: EDGAR 8-K `items` with acceptance times are not
stored today (the adapter reads the submissions field and drops it; T151 keeps it);
(2) the engine: no family reads an event table, and `earnings_drift` must enter
`HypothesisFamily` and `FAMILIES` by a reviewed code change behind a backtest spec
amendment, as `profitability` did (#720); (3) the registration path: the strategy lab's
migration (T113) is merged, so a new family's first registration is a **sweep file**
(strategy-lab spec req 1: `hypothesis register` refuses any standalone file after the
lab; the family's first registration is its first sweep). The owner therefore registers a
one-value sweep `docs/sweeps/b9-earnings-reaction-drift.md` whose fixed block reproduces
the parameter block below, runs it, and promotes its one variant to this file (which then
gains `## Sweep provenance`). Under research-program §4, promotion conditions 1 (data) and
2 (engine) are **not met today**; this file is condition 3, its trial budget is condition
4, and condition 5 is the owner's. The registry hashes the whole file, so any edit after
registration makes a new hypothesis.

Backlog item [B9](../research/hypothesis-backlog.md#b9-earnings-reaction-ear-drift). Claims
it tests, by id in [claims.toml](../research/claims.toml): **SH-6** (MIXED, the drift
after the announcement return), **SH-7** (revenue surprise alone, weak; the market-adjusted
reaction subsumes it), **SH-12** (T1 primary: the release time is in the free EDGAR
record). Disconfirmation it must survive: **SH-5** (NOT SUPPORTED: surprise-based PEAD is
gone outside microcaps since 2006) and **SH-2** (costs exceed most short-horizon spreads;
Chen & Velikov's 4 bp/month average). The research rules are in
[research-program.md](../ways-of-working/research-program.md).

## Economic rationale

**The claim.** A long-only book that buys, at the close of the three-session reaction
window, the stocks whose market-adjusted return around their earnings release sits in the
top fifth of recent reactions, and holds each for about twenty sessions, earns about the
market's return net of costs. The prior for its excess over SPY is **below zero at the
backtester's 15 bp per side**, and positive only at fills near 5 bp per side. That is not
a claim of an edge. The reasons to test it anyway are the owner's (2026-10-09): it is the
only short-horizon candidate with independent post-publication support in the
[short-horizon report](../research/2026-10-09-short-horizon-candidates.md) (rank 1), it
uses the data edge the project owns (exact SEC acceptance times, here of the 8-K Item
2.02 that carries the quarterly release), and it produces many trades at a daily cadence,
which is what a fast paper book under [ADR 0017](../decisions/0017-fast-paper-and-machine-readiness-gate.md)
(proposed, #1354) needs for the machine shakedown.

**Why the effect may exist.** The signal is the market's own reaction to the release
(Chan, Jegadeesh & Lakonishok 1996, the `AnnouncementReturn` construction in Chen &
Zimmermann), not an analyst or random-walk surprise. Since 2006 all price discovery after
an analyst surprise happens at the announcement in non-microcaps (Martineau, SH-5), so a
surprise measured against expectations predicts nothing afterwards; the drift that remains
in the report's evidence is on the **reaction** itself: investors underreact to a large
move on the day it happens and the move continues for a few weeks. The report grades it
MIXED because the post-publication signal is smaller, value-weighted, and insignificant
over 2019-2023 alone.

**Why it may be small or gone.** The register is explicit:

- SH-6: the value-weighted long-short decile spread fell from 1.34%/month in sample to
  0.99 after publication (1997-2024) and 0.81 in 2010-2024; the size-screened version
  (names above the NYSE 20th size percentile) is 0.25%/month (t 2.15) in 2010-2024 and
  its **long leg** +0.07%/month (t 0.94), gross, against the average portfolio. The
  value-weighted long leg is +0.32%/month (t 1.88). 2019-2023 alone is insignificant in
  both files.
- SH-2 and QI-2: the average anomaly earns about 4 bp/month net post-publication; a
  published effect loses about half its return after publication.
- Costs: the report puts a full monthly turnover at about 0.30%/month at 15 bp per side,
  "roughly the whole long-leg edge" (its Answer); Probe 3's +6 bp median fills were two
  megacaps against the official open, not the SIP bar open the backtester fills at
  (P3-5), so they do not establish 5 bp fills for a top-1000 book.
- Long-only is the binding constraint (the report's Caveats): every published number is
  long-short, and the short leg carries much of the spread.
- Our event is not the paper's: CZ keys on I/B/E/S dates; the 8-K 2.02 acceptance misses
  releases not furnished on an 8-K and catches 2.02 items that are not quarterly results
  (the report's "Event timing" caveat). T151's coverage count is the first measurement.

**Design.** An event book inside the ADR 0006 universe, expressed through the engine's
existing schedule: a `daily` cadence (ADR 0012) with a one-session hold, where the
*membership rule* ("an event of this name is live") carries the twenty-session hold, so
the engine needs no new schedule, only a new signal that reads an event table. The rules
below are the hypothesis's, written so the spec amendment, the engine tasks and the
look-ahead tests have one source.

### Point-in-time rules, stated so the auditor can check them

1. **An event is an 8-K whose `items` names 2.02, known at its acceptance.** The row
   comes from the `filing_events` table T151 adds (`cik`, `accession`, `form`, `items`,
   `accepted_at`), with `known_at` = the submissions `acceptanceDateTime` (UTC), never
   the filing index's `filed` date: an 8-K accepted at 20:30 New York carries the next
   calendar day's `filed` date, and keying on it would move day 0 a session late or, for
   a pre-open acceptance, make the window start before the release was public. The
   universe member is the CIK's listed class(es) through the master, as for every other
   filing-derived fact (data-foundation spec req 8).
2. **Day 0 is the first XNYS session whose open is after the acceptance instant**
   (`tradepartner.calendar` session opens, never 09:30 New York by arithmetic, never a
   weekday rule). Apple's 2026-07-31T00:30:28Z 8-K (SH-12) has day 0 = 2026-07-31; an
   8-K accepted at 08:00 New York on a session has day 0 that session; one accepted at
   16:30 has day 0 the next session.
3. **EAR is the adjusted close-to-close return from close(day −2) to close(day +2), minus
   SPY's over the same four sessions.** The [−1, +2] window of SH-6 (returns on sessions
   −1, 0, +1, +2). Prices are the engine's adjusted series (the `adjust.*` rules H1
   reads; `signal_total_return = true`); a name with no bar at close(day −2) or at any
   session of the window is excluded and counted (`n_excluded_no_window_bars`), never
   scored on a partial window.
4. **Breakpoints come only from windows closed at or before the read.** At read time
   `t = close(T)` the breakpoint set is the EAR of every universe event whose window
   closed within the trailing `breakpoint_lookback_sessions` (63, about a quarter)
   ending at T, with close(day +2) ≤ t. An event closing at T is scored against that
   set (itself excluded). Fewer than `min_breakpoint_events` (100) in the set means no
   entries that session, counted (`n_excluded_no_breakpoints`); that case is expected at
   the window's first sessions and in no later quarter.
5. **Entry at the window's close, fill one session later.** An event whose EAR is at or
   above the set's (1 − `top_fraction`) quantile (the 80th percentile: the top quintile)
   enters at the read T = day +2 and fills on session T + 1 at the family's frozen
   `execution.fill_price` (`close`, H1's and B3's convention, B9-3). That is one session
   later than the literature's entry at the open of day +3; the paper book fills after
   the open of T + 1 (Probe 3's 17-118 s after the open), between the two, and the
   tracking comparison measures the difference rather than assuming it away (ADR 0012
   sentence 2).
6. **Hold twenty reads, then exit.** An event is live at every read T_e, T_e + 1, …,
   T_e + 19 (`hold_sessions` = 20) and leaves the target set at read T_e + 20 (fill
   T_e + 21). A name is held while **any** of its events is live. A second 2.02 8-K from
   the same CIK within `event_dedupe_sessions` (30) of the previous one is ignored and
   counted (`n_events_deduped`): an 8-K/A, a second furnishing, a presentation furnished
   under 2.02. `event_forms` lists `8-K` only (B9-6).
7. **Equal weight across live names, each capped at `max_weight` (0.05), the remainder
   cash; re-targeted at every read.** The cap equals the paper risk rule
   `risk.max_position_weight` (ADR 0010), so no paper plan is refused for size. Under the
   daily cadence the engine re-targets equal weights every session, so drift between
   members is traded daily (the arithmetic is under Expected magnitudes). Fewer than 20
   live names leaves part of the book in cash; the live count and the cash share are
   counted per read (`n_held`, `cash_share`).
8. **The universe is ADR 0006's, read at `t`.** A name that leaves the universe (a
   delisting, the cap cut, the price floor) while live exits at the next read, as the
   engine's forced exits do; it is counted (`n_exited_universe`).
9. **No read-time derivations beyond the window return.** No standardised surprise, no
   volume condition, no analyst data (we hold none), no 10-Q or 10-K keyed fallback: a
   release first seen at the 10-Q is a different and untested event (ER-6, the report's
   "On keyed on 10-Q/10-K acceptance"), so a name whose results are not furnished on an
   8-K 2.02 is simply never an event here, and T151 counts how many universe names that
   is.

What the look-ahead suites must see for this family (backtest spec req 13, as the #720
amendment extended them for statement facts): truncation and prefix invariance with
`filing_events` in the truncated set; a revision case with teeth (an 8-K accepted
**after** close(T_i) whose event would enter at T_i: `run(end=T_i)` unchanged,
`run(end=T_{i+1})` changed); a breakpoint case (an event whose window closes after
close(T_i) and would move the 80th percentile at T_i: unchanged at T_i); a `filed`-date
trap (an 8-K accepted after the close whose `filed` date is the next day: day 0 is the
next session, never that day); and the plan-read timing check with the new plan fields
compared.

## Parameters

The block below is the only part the registry parses. It is **proposed**: the
`earnings_drift` section, the family name and its required-keys rule do not exist in
`backtest/hypothesis.py` or `config.py` (the spec amendment and engine tasks named in
#1358's PR body), so registering this file today fails on `family` and on unknown keys.
The file names the family's own section and no inert `strategy.*` keys (B3-1's rule, keyed
per family). The holdout, `in_sample_start`, universe and fill convention follow H1's and
B3's so the three families are one data-and-cost world, except where a root family sets
its own rule on purpose: the cost ladder gains a 5 bp rung (B9-4) because the report's
break-even sits there, and the holdout is **forward** (B9-2). Every other frozen key takes
its live config value at registration and is printed with the rest.

```toml hypothesis
slug = "b9-earnings-reaction-drift"
family = "earnings_drift"
title = "B9: long-only earnings-reaction (EAR) drift, top quintile, 20-session hold, daily"
in_sample_start = 2020-08-31

[holdout]
start = 2027-01-04
end = 2027-06-30

[earnings_drift]
event_forms = ["8-K"]
event_items = ["2.02"]
window_pre_sessions = 1
window_post_sessions = 2
hold_sessions = 20
top_fraction = 0.20
breakpoint_lookback_sessions = 63
min_breakpoint_events = 100
event_dedupe_sessions = 30
max_weight = 0.05
weighting = "equal"
signal_total_return = true

[schedule]
rebalance_cadence = "daily"
signal_anchor = "month_end"

[costs]
per_side_bps = 15.0
commission_per_share = 0.0
commission_per_order = 0.0
sensitivity_per_side_bps = [0.0, 5.0, 30.0, 60.0, 100.0]

[universe]
top_n_by_cap = 1000

[execution]
fill_price = "close"

[alpaca]
historical_feed = "sip"
```

Proposed answers, one line each (the owner confirms or changes them on #1358):

- **Cadence**: `daily` (ADR 0012). The twenty-session hold is a membership rule (rule 6),
  not a schedule: the engine's one-period hold with daily re-targeting reproduces it with
  no change to `backtest/schedule.py`. `signal_anchor` is inert for this family (it fixes
  the two bars momentum's score reads) and is pinned at its default so the frozen set is
  explicit.
- **Universe**: ADR 0006's top ~1000, `top_n_by_cap = 1000`, read at every close.
- **In-sample window under ADR 0016**: `in_sample_start = 2020-08-31` (H1's since #975,
  B3's since #1033: the first month-end at which the top-1000 cut binds in substance, the
  #974 listing bias being behind it) to the **development boundary 2023-12-29** (ADR 0016
  point 1, recommended value; the owner's `development_boundary` row when written; until
  then today's window rule ends the window at the last rebalance before `holdout.start`,
  which for this file's forward holdout would read into 2024-2026, so **this file is not
  registered before the boundary row exists**, B9-2). The months from 2024-01 to
  `holdout.start` are this family's dead months (ADR 0016 point 2), read by no run of
  its own. Why not earlier than 2020-08-31: the universe reason is H1's and applies
  unchanged (same store, same `universe_as_of`); events exist in the submissions record
  for the whole Alpaca price history (2016-01-04 on), so the data does not bind.
- **Forward holdout**: `[2027-01-04, 2027-06-30]`, the first XNYS session of 2027 to the
  last of June: 123 sessions, 6 month-ends. It is forward (ADR 0016 point 4: no session of
  it exists at registration), so its exam of record is **this family's paper book** over
  at least `paper.min_rebalances` rebalances in the window's cadence units (ADR 0017 C;
  its open question 2 recommends 63 at `daily`, a quarter; 123 sessions leave room), under
  the ADR 0005 tracking check. It must start after 2026-09-30 (the strategy-lab overlap
  rule: `momentum` and `profitability` hold out [2024-01-01, 2026-09-30]) and after the
  registration day. January 2027 leaves a quarter for T151, the spec amendment, the family
  tasks, the sweep registration and the in-sample run; the owner may move both dates, and
  a later start is always allowed. A book may run past `holdout.end` as an ordinary Phase
  4 window.
- **Costs**: the spec's placeholder base of 15 bp per side and H1's ladder, **plus a 5 bp
  rung** (B9-4): the report's whole verdict on this candidate turns on whether fills sit
  near 5 or near 15 bp, so the trial should print both. Sensitivities never select (spec
  req 6); the base reading is the one the retirement rule reads. A root family may set its
  own ladder (strategy-lab spec, Definitions: the family rules are fixed at first
  registration); a later registration in this family may raise the base, never lower it.
- **Statistical threshold**: none (spec open question 10 (a)); the retirement condition is
  in words below.
- **Trades per month** (the report's estimate, universe size times the top quintile of
  about 4,000 announcements a year): about **65 entries and 65 exits a month on average**,
  seasonal: 3 to 8 orders a session in the six peak weeks of each quarter, 0 to 2 a
  session between seasons, each side; plus the small daily drift trades of rule 7. The
  paper run's `risk.max_orders_per_run` (250) is far above any session's count.
- **Data each rule needs**: rule 1, the 8-K `items` field with acceptance (**T151**, not
  held today); rules 2 and 4, the XNYS calendar (held); rule 3, bars and corporate
  actions through the engine's adjusted series and SPY's bars (held; SPY is a seeded
  benchmark); rule 7, nothing new; rule 8, `universe_as_of` (held). Nothing needs a
  vendor, intraday data or analyst data.

## Expected magnitudes and red flags

**No figure below is from a long-only, top-1000, net test**, because none exists; the
register holds long-short gross spreads and the report's long-leg proxies (top portfolio
minus the mean of the signal's portfolios, not minus SPY). The prior is written to be
disconfirmable, with the arithmetic shown so the owner can replace any input.

**Gross long leg.** +0.07%/month (size-screened, t 0.94) to +0.32%/month (value-weighted,
t 1.88) over the average portfolio in 2010-2024 (SH-6), that is **about +0.8 to +3.8
pp/yr** before costs, with the truth for the ADR 0006 universe probably between the two
files (the report's "Universe mismatch"). The 2019-2023 figures alone are insignificant.

**Costs at our scale, stated plainly.** Entries and exits turn the book over about once
a month (a twenty-session hold is about a month), so one-sided turnover is about
**100%/month**: at 15 bp per side that is 2 × 100% × 15 bp ≈ **0.30%/month, about 3.6
pp/yr**, the whole gross long leg in the size-screened file and most of it in the
value-weighted one. Daily re-targeting to equal weight adds a drift cost: with about 65
names at about 1.5% each and a 1.5% daily idiosyncratic dispersion, each name's weight
drifts by about 0.02 pp a session, about 1.5% of NAV traded a session across the book,
0.75% one-sided, 16%/month one-sided, **about 0.05%/month at 15 bp**. The net prior over
SPY at the base level is therefore **centred at about −1 pp/yr, plausible range −5 to +2
pp/yr**, positive only at the 5 bp rung, and at the handoff's HO-14 floor (5-10 bp plus
half the spread) at or below zero, which is the report's verdict restated: "~zero net
long-only at 15 bp per side". The cash share under the 5% cap (rule 7) is a second drag
in a rising market: between earnings seasons the book may hold 10 to 20 names and 0 to 50%
cash, so part of any shortfall against SPY is exposure, not selection, and `cash_share` is
reported per read so the two can be told apart.

**Tracking error against SPY.** Unknown; assumed **10%/yr** for the power arithmetic: a
20-to-65-name book of names that just moved on news, with a seasonal cash share, is
noisier than H1's 100-name decile (8.4%) or B3's 60-name annual book (6%).

**Turnover and cost drag.** One-sided monthly turnover of about 100 to 120% (entries and
exits plus drift), seasonal with the earnings calendar; cost drag at 15 bp of roughly 3.5
to 4.5 pp/yr, at the 100 bp rung roughly 24 to 30 pp/yr. A `cost_drag` far from
≈ 12 × `turnover_monthly` × 2 × `per_side_bps` is a cost-model bug.

**Losses.** A long-only, event-concentrated equity book: a full-crisis drawdown about the
market's, with a seasonal exposure that may make it shallower or deeper by accident. No
source in the register gives a worst quarter for this construction; the run computes it.

**Red flags** (a prompt for a look-ahead and data audit, never a gate):

- Base-level `excess_cagr_spy` above `metrics.red_flag_excess_cagr_pp` (3.0): spec req 15
  marks the trial `red_flag`. Nothing in the register supports a net long-only excess of
  that size; a value-weighted long-short spread of 0.81%/month does not translate into it.
- Day 0 derived from `filed` instead of `accepted_at`, or a breakpoint set that includes an
  event whose window closes after close(T): both are look-aheads, and both are what the
  revision and breakpoint cases above exist to catch.
- `n_held` above about 150 or below 10 for more than two weeks inside an earnings season
  (February, late April to May, late July to August, late October to November): the
  event feed, the dedupe rule or the breakpoint quantile is wrong, not the market.
- One-sided `turnover_monthly` far below 70% or far above 150%: the hold length or the
  membership rule is not doing what rule 6 says.
- `n_excluded_no_breakpoints` non-zero after the first quarter of the window: the event
  feed has a hole (T151's coverage count is the baseline).
- An EAR computed across a split without adjustment (a ±50% or ±90% "reaction" on a
  split's ex-date): rule 3 reads the adjusted series, and such a value is the proof it did
  not.
- A result that changes when the run is truncated (truncation and prefix invariance), or a
  revision case that does not change the later run: look-ahead, or a harness without
  teeth for this table.
- A survivorship gap above `gap.count_share_threshold` at any read, a non-zero
  `n_static_listings`, or an `n_universe` below `universe.top_n_by_cap`: a biased or
  holed universe, as for H1 and B3 (their red-flag bullets say why).

## Power arithmetic

Rebalance sessions are counted with `backtest.schedule.rebalance_sessions` on the XNYS
calendar at the `daily` cadence (the counts here were taken with `exchange_calendars`
XNYS over the same ranges; the family's test file pins them when it exists).

- **In-sample run:** `in_sample_start` to the last session on or before the boundary:
  2020-08-31 to 2023-12-29, **839 reads and 838 daily period returns** (the first fill is
  2020-09-01; the 2023-12-29 read fills on 2024-01-02, a dead month, so its period is not
  in sample), about **3.33 years**. The same calendar span holds 174 week-ends and 41
  month-ends (H1's and B3's 41).
- **Forward holdout:** `[2027-01-04, 2027-06-30]`, **123 sessions**, about half a year;
  judged by the paper book, not by a backtest spend, unless the owner decides ADR 0016's
  open question 2 (a backtest spend once the months have passed) the recommended way.

**t-statistic** (ADR 0005: t ≈ SR × √years, the excess Sharpe being the excess return
over its tracking error), at the assumed 10%/yr tracking error:

| Window | Periods | Years | t for +1 pp/yr at 10% TE | Excess needed for t ≈ 2 |
|---|---|---|---|---|
| In-sample | 838 daily | 3.3 | **0.18** | **11 pp/yr** |
| Forward holdout | 123 daily | 0.5 | 0.07 | 29 pp/yr |
| Both | 961 daily | 3.8 | 0.20 | 10 pp/yr |

Daily periods do not add power: t depends on the span in years, not on the number of
periods, so the 838 returns buy a better volatility estimate and nothing else. Any
in-sample excess large enough to be significant (about 11 pp/yr) is more than three times
the red-flag threshold, so it would read as a bug before it read as an edge. **The test
cannot reach significance for any result the prior allows.** What the in-sample run can
show: whether the engine reads events point-in-time (the look-ahead suites extended to
`filing_events`), whether the event coverage, the held count, the cash share and the
turnover sit inside the prior, whether the gross (0 bp rung) long leg is positive at all,
and how far the 5 bp and 15 bp rungs sit apart, which is the number the paper book then
tests with real fills.

**Deflated Sharpe** (spec req 8) is reported on both bases with N and V over the
`earnings_drift` family's `ok`, non-synthetic, in-sample trials. This file's first trial is
the family's first, so N = 1 (plus any `return`-kind research runs the research registry
adds), SR* = 0 and DSR reduces to PSR. The family is a **root** (B9-1): its N never
includes `momentum`'s or `profitability`'s trials and theirs never include its, and its
SR* high-water mark starts at zero.

**Trial budget** (research-program §4 item 4; QI-11: every variant counted). Proposed, for
the owner (B9-7): this registration (one sweep variant), one run over the default in-sample
window, the paper book as its forward exam, and at most **three pre-declared variants**,
each a new sweep variant in this family and counted in its N: (i) `hold_sessions = 60`
(Martineau's BHAR[2, 60] horizon; about a third of the turnover); (ii) `top_fraction =
0.10` (the decile, closer to the CZ spread's construction); (iii) `weighting = "hold"`
(weights set at entry and left to drift, no daily re-targeting, which removes the drift
cost of rule 7) if the family's spec amendment adds that literal. Not in the budget, and
not variants: a 10-Q or 10-K keyed version (a different event, a new hypothesis); a short
leg (charter); a surprise-based score (SH-5). Reruns for logged bugs are counted as the
spec counts them. Nothing else runs in this family without a backlog entry and an owner
decision.

## Prior-evidence disclosure

Every result for the holdout period already seen before this draft. The holdout is
forward (January to June 2027), so **no price, return or fill of it exists anywhere**, and
"seen" below means the evidence that shaped the design and the dead months the design
cannot unsee.

**From the research report.** The report's own Chen & Zimmermann computation
(`AnnouncementReturn`, value-weighted deciles and the size-screened file) covers
1997-2024, 2010-2024 and 2019-2023, so the candidate was selected on windows that include
**2024**, after the development boundary; the report's Caveats say so and this file
repeats it: **2024-2026 is never out-of-sample evidence for this hypothesis**, and those
months are its dead months, read by no run of its own. Dai et al. (2001-2021) and
Novy-Marx & Velikov (to 2013) are inside the development years. No figure for 2025, 2026
or 2027 appears in any report in `docs/research/`.

**Benchmark-period facts already seen through H1's and B3's disclosures** (2024 and 2025
MSCI USA, Russell 1000 and MTUM returns) describe the dead months, not the holdout, and are
listed there.

**General knowledge, owner and agents.** The owner follows the market. The agents that
wrote this file and its sources are language models whose training data covers market
news through mid-2026, including published earnings-reaction and event-drift results over
the period; no figure from that memory is written here, because none can be cited, and
nothing in the design was tuned on it. The dead months are not unseen in that sense; the
holdout months do not exist yet.

**Not seen.** No TradePartner trial of this family exists; no store holds `filing_events`;
no coverage spike has been run (T151's coverage count is the first). The Probe 3 fills
(P3-1 to P3-5) are two megacaps on two sessions in October 2026 and are execution facts,
not returns.

## Retirement condition

Stated before any run, per spec open question 10 (a), and read the way H1's and B3's are.

**The reading is fixed in advance.** The condition reads two stored values of one trial:
the base-level `excess_cagr_spy` and the run-time `dsr_excess` in `trial_results` (N and
V at run time). The trial is the **first** `ok`, non-synthetic, `in_sample` trial of this
hypothesis over the full default window [2020-08-31, 2023-12-29] under the development
boundary that passed `quant-auditor`. A later trial replaces it only when an audit logged a
bug in the earlier one (a look-ahead in the event read or the breakpoints, a day-0 defect,
an adjustment defect). Shorter or later-start runs never count. The paper book's tracking
check neither retires nor promotes B9; it is the forward exam of record and is reported.

**B9 retires as a candidate for live capital** (Phase 6) when that trial's net excess CAGR
over SPY is below **−1 pp/yr** **and** its `dsr_excess` is below 0.5. It is the same line
H1 and B3 drew, chosen so the three families are read alike; each pp/yr of excess is worth
about t ≈ 0.18 here, so the line is inside one standard error of zero and inside the
prior's range. Its job is to be a line drawn before the run. Retirement ends the line: a
later revisit is a new hypothesis with its own registration and budget (research-program
§6).

**The cost-bound reading is pre-declared, and it is a diagnostic, not part of the rule.**
The same trial's sensitivity rungs are stored (spec req 6). If the base reading retires B9
while the **0 bp rung's** excess over SPY is positive, the `TP-` claim that records the
result says "gross long leg positive, cost-bound at 15 bp" rather than "no signal", and
names the 5 bp rung's value. Whether the paper book opens anyway, to measure real fills on
this many orders, is then an owner decision under ADR 0017 (a book is an operations test
plus evidence, part A), not a promotion: a retired hypothesis stays retired as a live
candidate. Sensitivities never select (spec req 6), and this reading selects nothing; it
labels.

**Nothing promotes B9.** No result passes it, and no DSR value is a threshold. A go-live
decision needs the paper book's tracking check and the Phase 6 gates (ADR 0017 F), and it
is never made by comparing paper books (ADR 0016 point 3).

**A red-flagged trial neither retires nor promotes.** It opens a look-ahead and data
audit, and the hypothesis stays unresolved until the audit ends.

**A variant is a new hypothesis.** Changing any frozen value, including the window, the
hold, the quantile, the breakpoint rule, the dedupe rule, the cap or the weighting, is a
new sweep variant with a new slug, counted in the family's N. This file is never edited
after registration to fit a result.

## The family question (no code in this PR)

**Recommendation: a new root family, `earnings_drift`.** The signal is unrelated to
momentum's and profitability's (strategy-lab spec open question 11, decided (a): "an
unrelated signal is a root family"), it reads a table no family reads, and its holdout
cannot be `momentum`'s (spent) or `profitability`'s (unspent, on hold): under the overlap
rule a new family's holdout may not overlap [2024-01-01, 2026-09-30], so it is forward by
construction (ADR 0016 point 4), and a root family takes exactly one queue slot in the
forward-exam calendar ("new families' exams queue one after another in calendar time",
ADR 0016's Consequences). The alternative, a child of `momentum`, would inherit momentum's
SR* mark and show its N for no reason the signal gives. Because B10's recommendation is a
`momentum`-family amendment, not a second root family, B9 is the only new family these two
files create, so no two forward holdouts need sequencing (B9-1).

**What it would need** (sizes and files are estimates for the plan that follows an accepted
spec amendment; none of it is built here):

1. **A backtest spec amendment** (class B, `spec-critic`), as #720 was for `profitability`:
   the family, its section and keys (the block above), the point-in-time rules 1 to 9, the
   counts (`n_events`, `n_entries`, `n_exits`, `n_held`, `cash_share`,
   `n_excluded_no_window_bars`, `n_excluded_no_breakpoints`, `n_events_deduped`,
   `n_exited_universe`), the exclusion reasons, the look-ahead cases above, and whether
   `weighting = "hold"` exists at all. Size S.
2. **Config and the family entry:** `config.py` gains `earnings_drift` in `HypothesisFamily`
   and `FAMILIES` (`sections = ("earnings_drift",)`, an `EarningsDriftConfig` params model,
   `parent = None`, `engine_ready = True`, `paper_ready = False` until ADR 0014 point 5's
   test exists, the reasons and counts, `benchmark = None` or SPY only), with the
   `FAMILY_PARENTS` entry it derives; a new section is inert for other families
   (`backtest/hypothesis.py`), so H1's, B3's and the T114 sweep's fingerprints do not move,
   and `tests/test_config.py` pins that. Size S. `quant-auditor`, `safety-reviewer`
   (`config.py` is on both lists).
3. **The as-of read:** `store/asof.py` `filing_events_as_of(t, forms, items)` over T151's
   table (`known_at ≤ t`), returning one row per listed class of a CIK as
   `statement_facts_as_of` does. Size S. `quant-auditor`.
4. **The signal:** a pure function in `backtest/signals.py` (events → day 0 → window
   returns → breakpoints → live set → target weights with the cap), with the calendar's
   session opens and the adjusted price frame as inputs, and its read in
   `backtest/strategies.py` dispatching on the family; the look-ahead cases in
   `tests/lookahead/`. Size M. `quant-auditor`. No change to `backtest/schedule.py` or the
   engine's hold: the daily cadence and the membership rule carry it.
5. **The paper side:** `execution/strategies.py` and `execution/planning.py` read the same
   table and dispatch on the family; the `paper_ready` flip lands with a fixture-store
   round-trip test (ADR 0014 point 5; ADR 0017 D's pattern), class A, labelled `hold`.
   Size S. `safety-reviewer`, `quant-auditor`. Its book needs ADR 0017 B (its own key pair
   and account) and C (`daily` on paper), neither of which this file depends on for the
   in-sample run.
6. **The sweep file and registration** (owner): `docs/sweeps/b9-earnings-reaction-drift.md`,
   one variant, after the `development_boundary` row exists; then `sweep run`, the audit,
   and the promotion to this file.

## Open questions for the owner (B9-1 to B9-7; none decided)

- **B9-1. The family.** (a) A new root family `earnings_drift` (above, recommended); (b) a
  child of `momentum` (inherits the mark and the N for no reason the signal gives; its
  holdout must still be forward); (c) a generic `event` family whose section names the
  form and items, so a later 8-K keyed signal (Item 5.02 departures, Item 8.01) is a
  sweep variant rather than a family. Recommendation: (a), with the section keys written
  so that (c) is a rename later, not a redesign: `event_forms` and `event_items` are keys
  for that reason.
- **B9-2. The forward holdout and the boundary.** `[2027-01-04, 2027-06-30]` as proposed,
  or other dates. Constraints: after 2026-09-30 (overlap), after the registration day
  (forward), at least `paper.min_rebalances` daily rebalances long once ADR 0017's open
  question 2 sets that default (63 recommended). The file is not registered before the
  owner writes the `development_boundary` row (ADR 0016 point 1; T142b's command): with
  no row, today's rule would end the default window at the last rebalance before
  2027-01-04 and read 2024-2026 in sample. Recommendation: the dates as proposed; write
  the boundary row first.
- **B9-3. The fill convention.** `close` (H1's and B3's; the engine fills at close(T + 1))
  or `open` (`execution.fill_price = open` exists; the literature's entry; Alpaca's daily
  open is "the first valid trade", T3's reason for `close`, and the SIP bar open differs
  from the official open by −4 to +20 bp, P3-2). The fill convention is a family rule fixed
  at first registration, so this is decided once. Recommendation: `close`, one world with
  H1 and B3, and let the tracking comparison measure the open-fill paper book against it.
- **B9-4. The cost ladder.** H1's `[0, 30, 60, 100]` plus a `5.0` rung (proposed), or
  H1's unchanged. Recommendation: add the rung; it is the number the report's verdict turns
  on and it costs nothing.
- **B9-5. The position cap.** `max_weight = 0.05` (equal to `risk.max_position_weight`,
  so the book is fully invested from 20 live names up and never refused on paper for
  size), `0.02` (fully invested from 50 names; more cash between seasons), or no cap
  (fully invested always; 5 to 10 names at 10 to 20% each between seasons, which the paper
  risk rule would refuse). Recommendation: 0.05.
- **B9-6. The event definition.** `event_forms = ["8-K"]` with a 30-session dedupe
  (proposed); or 8-K and 8-K/A; or a 10-Q/10-K acceptance fallback for names with no 2.02
  8-K. Recommendation: 8-K only, no fallback (a different event, ER-6); T151's coverage
  count says how many universe names the rule leaves out, and a fallback, if ever wanted,
  is a new hypothesis.
- **B9-7. The trial budget.** This file plus the three pre-declared variants above, or a
  different set. Recommendation: as proposed, with (iii) conditional on the spec amendment
  adding `weighting = "hold"`.
