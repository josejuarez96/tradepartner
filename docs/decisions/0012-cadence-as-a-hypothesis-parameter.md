# 0012. Cadence as a hypothesis parameter

**Status:** Accepted (by merging PR #TBD, a class-B merge by the owner; the text is the [strategy-lab spec](../specs/strategy-lab.md)'s "ADR 0012" paragraph, Accepted by the owner on 2026-10-05, #931)  ·  **Date:** 2026-10-05  ·  **Issue:** #945 (plan task T92; spec #281, plan #933)  ·  **Supersedes:** the Cadence section of [ADR 0006](0006-universe-and-cadence.md) only; its universe section, guards (a) and (b) and its Verify section stay in force  ·  **Related:** [strategy-lab spec](../specs/strategy-lab.md) (Definitions "Cadence", "Period", "Signal anchor"; reqs 6, 7, 9 and 11), [strategy-lab plan](../plans/strategy-lab.md) (T94, T95, T96 and every cadence task behind them depend on this ADR), [backtest spec](../specs/backtest.md), [paper-trading spec](../specs/paper-trading.md) req 14, [ADR 0005](0005-objective-benchmark-stop-criteria.md) (unchanged), [charter](../charter.md) Scope

## Context

ADR 0006 fixed the rebalance cadence as a project constant: the last trading session of each calendar month, a one-month hold, "not config". That matched the momentum evidence (monthly formation, one-month skip) and kept the MVP's cost model simple and its trial count small. It also meant a weekly or daily hypothesis could not be registered at all, so the registry could never answer "which cadence pays for its turnover".

The strategy-lab spec (Problem) wants strategies developed in volume while H1's six-month paper window runs: hundreds to thousands of registered runs, and rebalance cadences faster than monthly. Its Definitions make the schedule a frozen hypothesis parameter rather than a constant:

- **Cadence**: the frozen `schedule.rebalance_cadence` ∈ {`month_end`, `week_end`, `daily`}. Rebalance session T_i: under `month_end` the last XNYS session of each calendar month (the Phase 3 definition, unchanged); under `week_end` the last XNYS session of each ISO week (Monday to Sunday); under `daily` every XNYS session. All three come from `tradepartner.calendar` over `all_sessions()`, never from weekday arithmetic. Fill session F_i = `calendar.next_session(T_i)` and read time close(T_i) are unchanged at every cadence; at `daily`, F_i = T_{i+1}. A window whose end is not a rebalance session rebalances last at the last rebalance session ≤ end. With `execution.fill_price = close` a trade decided at close(T_i) fills at close(F_i), one session later, at every cadence (H1 included), with full exposure. `daily` is the floor: the store holds daily bars, so nothing intraday exists or is specified (out of scope without a data ADR).
- **Period** i: the interval (T_i, T_{i+1}] between consecutive rebalance sessions; period returns are the unit for Sharpe, skew, kurtosis and the DSR's T. `PERIODS_PER_YEAR` = {`month_end`: 12, `week_end`: 52, `daily`: 252} is a constant table in `backtest/schedule.py` derived from the cadence, not config; `MONTHS_PER_YEAR = 12` is its `month_end` entry.
- **Signal anchor**: the frozen `schedule.signal_anchor` ∈ {`month_end`, `offset`}, which fixes the two bars the momentum score reads at T (spec req 7). `month_end` is the Phase 3 rule and H1's.

The forces, beyond the spec's own:

- **Charter principle 2 and ADR 0005's integrity criterion.** A cadence chosen per hypothesis is a registered trial in the family's N, never a knob turned after a run; the spec counts a cadence sweep as one hypothesis per cadence. This is why the schedule is frozen per hypothesis exactly as ADR 0006 guard (a) freezes the universe.
- **Pre-lab registrations.** H1 was registered without `schedule.*` keys. The spec's frozen-key defaults (`FROZEN_KEY_DEFAULTS`, read through `frozen_values`) make it read `month_end`/`month_end` at its stored hash; nothing is re-registered, and its trials, `gap_signoff` and paper window are untouched.
- **Phase 4 stays monthly.** `paper start` refuses a hypothesis whose cadence is not `month_end` (spec req 11); ADR 0005's six-rebalance criterion and the paper spec's monthly tracking comparison are as written.
- **The broker bound of ADR 0006 still holds.** Alpaca fractional orders are DAY-only, so daily-or-slower remains the only cadence that fits the broker; `daily` is the floor here for the data as well.
- **Who may change the charter.** The charter's Scope line cites ADR 0006 for the cadence and changes only through an ADR, which is this one.

## Options considered

**Cadence** (ADR 0006's options, with the strategy-lab spec's added sentence):

1. **Daily rebalance.** Con (ADR 0006): turnover and costs dominate a long-only momentum signal; more trials per year.
2. **Quarterly.** Con: weaker match to the momentum literature; slower feedback for a learning project.
3. **Monthly, one-month hold** (ADR 0006's choice, the MVP's). Matches the evidence and the paper-trading feedback loop.

Daily and weekly cadences are testable hypotheses in the backtest under the strategy-lab spec; the monthly choice stands for the paper and live vehicle.

**Periods per year** (strategy-lab spec open question 3):

1. **Constants 12 / 52 / 252** (this ADR, the spec's default). Pro: H1's stored `cagr` is unchanged, one table in one module, the Phase 3 reference values hold for an all-monthly family. Con: `cagr`'s years = n/52 overstates elapsed time by about 0.34% against the calendar at `week_end`; under one percent at every cadence.
2. **Computed from the calendar over the run** (sessions per calendar year, ISO weeks per year). Pro: exact elapsed time. Con: changes H1's stored `cagr`; a per-run figure where the spec wants a stored constant a reader never guesses.
3. **Constants for Sharpe and volatility, calendar years for `cagr`.** Recorded by the spec as a retro item, not taken now.

**Signal anchor** (strategy-lab spec open question 12):

1. **Two keys: `schedule.signal_anchor` ∈ {`month_end`, `offset`}, default `month_end`** (this ADR, the spec's default). Pro: the cadence and the anchor are two named keys, so a sweep can vary them separately and the confound is visible in every report row. Con: one more frozen key, with a behaviour-preserving default in `FROZEN_KEY_DEFAULTS`.
2. **`offset` at every non-monthly cadence, no key.** Pro: nothing to pin. Con: a weekly hypothesis could never be compared with and without the month-end anchor, and the anchor would change silently with the cadence.

The spec records questions 3 and 12 as standing defaults, not owner decisions; this ADR carries those defaults, and the owner may still override either on #809 before the task that implements it (T94 for the constants, T95 for the anchor).

## Decision

We will make the rebalance cadence a frozen hypothesis parameter. The sentences below replace ADR 0006's Cadence section, bullet for bullet; they are the strategy-lab spec's "ADR 0012" paragraph, verbatim. The rest of ADR 0006 stands.

1. Rebalance date T is set by the hypothesis's frozen `schedule.rebalance_cadence`: `month_end` (the last trading session of each calendar month on the XNYS calendar, the default and the MVP's), `week_end` (the last XNYS session of each ISO week) or `daily` (every XNYS session). The signal and the universe are read at close(T) at every cadence; the backtest fills on session T+1 at the hypothesis's frozen `execution.fill_price`; paper execution is the paper spec's. Paper and live trading of the MVP stay `month_end` until a Phase 4 spec amendment names another cadence.
2. Signals and universe use closes through T (ADR 0003 rule 2). The backtest and the `bt` oracle fill on session T+1 at the frozen `execution.fill_price` (`close` since Phase 2 T3 for H1); how paper executes on T+1 is the paper spec's, and the difference between the two is measured by its tracking comparison, not assumed away.
3. Holding period is one rebalance period: positions are held until the next rebalance. No intra-period trades except forced exits (delisting, kill switch, risk rule).
4. **Missed rebalance** (machine off, source stale, broker down): trade at the next available session, logged as a missed rebalance in the journal. The backtester does not model this; paper-vs-backtest drift from missed rebalances is reported separately. *(Carried over from ADR 0006 unchanged.)*
5. Position count and weighting (equal-weight top decile, etc.) belong to the strategy spec in Phase 3, not this ADR. *(Carried over from ADR 0006 unchanged.)*

**Benchmarks** (ADR 0005) use the same calendar and the same frozen fill convention as the strategy.

**Guards.** ADR 0006's guards (a) and (b) stay in force. This ADR adds **(c)**: `schedule.rebalance_cadence` and `schedule.signal_anchor` are frozen per hypothesis like the universe; a cadence sweep registers one hypothesis per cadence, counted in the family's N; the fill convention, the cost base, the universe and the window are family rules fixed at the family's first registration.

**Where it lives** (the spec's reqs 6 to 9 and 11; built by the strategy-lab plan's T93 to T97 and T100): two frozen config keys, `schedule.rebalance_cadence` and `schedule.signal_anchor`, both defaulting to `month_end` and both in `FROZEN_KEY_DEFAULTS`, so every pre-lab registration reads `month_end`/`month_end` at its stored hash; `PERIODS_PER_YEAR` and `rebalance_sessions(start, end, cadence)` in `backtest/schedule.py`; `calendar.last_session_of_week` and `calendar.rebalance_sessions_between` over `all_sessions()`; metrics in period units with `periods_per_year` stored on every trial and V in annual units; `paper start` refusing `refused_cadence` for anything but `month_end`.

**The charter's Scope line** reads, from this ADR on: "**Cadence** (ADR 0012, superseding ADR 0006's cadence): a frozen hypothesis parameter, `month_end` by default (the last session of the month, orders for the next session at the frozen fill convention, one-period hold); the MVP's paper and live cadence is monthly."

**ADR 0006's pending amendment draft** (T70, PR #297, parked at the time of writing) concerns how paper executes on T+1 under H1's frozen `close` convention. It amends ADR 0006 and agrees with sentence 2 here, which leaves paper execution to the paper spec. Should #297 land first, its note stays on ADR 0006 and this ADR's PR merges `main` in and keeps both notes (strategy-lab plan, T92's line); nothing in the sentences above changes.

## Consequences

- Good: a cadence is a counted hypothesis parameter, not a project constant, so "which cadence pays for its turnover" becomes a registered experiment with the family's N, V and holdout rules unchanged; the MVP is untouched (H1's row, hash, trials and paper window stand; the Phase 3 reference values hold for an all-monthly family); Phase 4 and ADR 0005 need no change; cadences are compared in one unit (annualised Sharpe, `turnover_annual`, `cost_drag`); the three schedules come from one calendar function, never weekday arithmetic; the anchor and the cadence are separately sweepable.
- Bad / accepted risks: **the DSR formula and the √ppy conversion assume Sharpe scales with √k under aggregation and that period returns are not autocorrelated.** With the same annual Sharpe (0.866) and the same annual SR* (0.658), the reference DSR is 0.727 monthly, 0.737 weekly and 0.741 daily: the rise comes only from T in the formula, not from new information, because the extra periods are not independent draws to the extent the formula assumes. The annual-unit V makes cadences comparable in N and V but not identical in power, and the report labels DSR per variant with its cadence (strategy-lab spec, Risks). `cagr`'s years = n/52 overstates elapsed time by about 0.34% at `week_end` (option 1 above). No market impact is modelled at any cadence, so `daily` results at the base cost level are optimistic; the sensitivity ladder bounds them. A `daily` step is a universe rebuild, a gap read and a price read per session, about 21 times the monthly count; the plan's measurement task (T114) turns that into numbers before the time budget is set. Month-end flows may worsen `month_end` fills (ADR 0006's risk, unchanged for the paper vehicle).
- Reversibility: adding a cadence value is one reviewed change to the `Cadence` literal, the `PERIODS_PER_YEAR` table and the calendar helper, with a spec amendment. Removing one is a one-way door once a hypothesis at that cadence is registered: trials are never deleted, and `FROZEN_KEY_DEFAULTS` is append-only and immutable, so the `month_end` defaults can never move. Reverting to ADR 0006's constant would mean refusing new non-monthly registrations, not unregistering any.
- Revisit if: a non-monthly hypothesis is promoted toward paper (Phase 4 spec amendment); the measurement task shows a `daily` sweep is impractical on the owner's machine within the quiet intervals; an intraday data ADR is proposed (`daily` is the floor until then); or a cadence-axis sweep's report shows the autocorrelation caveat above biasing the comparison in practice, which would reopen open question 3 (calendar-based periods).
