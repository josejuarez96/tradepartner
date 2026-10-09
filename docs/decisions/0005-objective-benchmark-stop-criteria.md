# 0005. Objective, benchmarks and stop criteria

**Status:** Accepted  ·  **Date:** 2026-09-24  ·  **Issue:** #7

## Context

The [charter](../charter.md) left the objective, benchmarks and stop criteria open. The [handoff](../research/2026-09-24-initial-research-handoff.md) makes two facts unavoidable:

- **An edge cannot be proven at this scale.** Sampling error on a Sharpe ratio gives t ≈ SR × √years (D1). Confirming an excess Sharpe of 0.3 over a benchmark takes ~44 years; around 100 trades is mostly noise (§4).
- **Long-only momentum already exists as a product.** MTUM charges 0.15%/year for roughly the MVP strategy (H1). A homemade version beating it after costs and taxes is unlikely.

So a P&L objective would be unmeasurable, and a benchmark-beating objective would be a bet against a cheap ETF. What *is* measurable within the project's life is whether the system is correct, honest and disciplined.

## Options considered

1. **Beat a benchmark.** Pro: simple to state. Con: unmeasurable in the available time; invites strategy-hopping after noise; the research says the retail edge, if any, is defensive.
2. **Learning and process quality, with benchmarks as instruments** (this ADR). Pro: every criterion is checkable by a command; matches the charter's purpose; the benchmarks still expose a broken system. Con: it is possible to "succeed" while losing money, which must be stated plainly.
3. **Calibration only** (Brier scores on probabilistic calls). Pro: rigorous. Con: needs the LLM layer or human forecasts, both deferred; too narrow on its own.

## Decision

**Primary objective, ranked:** (1) learn to make disciplined, evidence-based decisions, demonstrated by process metrics; (2) calibration, once anything in the system emits probabilities; (3) beating a benchmark, explicitly optional and not a criterion for any phase exit.

**Benchmarks:** SPY and MTUM, both total return (dividends reinvested), computed inside the system from the same `PriceSource` and with the same cost model as the strategy. They are measured against, never targeted. A backtest or paper result is reported only alongside both.

**Success criteria** are the phase exit criteria in the [roadmap](../roadmap.md). In addition, at the end of Phase 4:
- after at least **6 paper rebalances** (`paper.min_rebalances`), the **maximum absolute difference** between the paper and backtest monthly returns on the same dates is ≤ `paper.tracking_k` × the modeled cost per rebalance (proposed k = 2), excluding months flagged as missed rebalances, which are reported separately;
- every paper order has a journal chain signal → decision → order → {fill | expired | rejected | cancelled} → outcome, with those four as the only allowed terminal states, verified by a query;
- the override log has a reason on every row;
- the engine **cannot run without a trial ID**: it obtains one from the registry before computing and refuses to return results otherwise, so "every run is registered" holds by construction and is verified by a test that the engine raises without an ID.

**Stop / failure criteria** (set now, checked at every phase retro):
- **Time:** if no phase exits within 6 calendar months of the previous phase tag, the retro must record an explicit continue-or-stop decision by the owner. Weekly hours are not fixed; the owner records actual build hours and review hours separately in each retro. **Machine assumption:** the system needs a local machine that is on and online after each session close (ingest) and before the open of the first session of each month (orders); misses follow ADR 0006's missed-rebalance rule.
- **Spend:** the charter's budget ceiling, scoped to data, API and LLM spend **by the running system** (development tooling such as the owner's Claude subscription is excluded). The owner records the month's spend as a row in the retro or the database, so the check is a query. Exceeding the ceiling in any month halts paid ingestion until the owner amends the charter.
- **Live capital:** ~$100 and never topped up without a charter amendment. A total loss of that amount is an accepted outcome, not a stop trigger. Risk rules (Phase 4 ADR) handle drawdown responses; they are operational rules, not success metrics.
- **Integrity:** any discovered look-ahead in a result that was already reported, or any untracked backtest run, halts the current phase until fixed and retro'd.

## Consequences

- Good: objective (1) is measured by exactly the four Phase 4 checks above, each a command or query; the time and spend criteria are retro judgments against recorded numbers; the owner cannot fool themselves with a lucky quarter; benchmarks catch a broken system without being a moving target.
- Bad / accepted risks: the project can be a success by its own criteria while underperforming SPY. The owner accepts this. If the owner later wants a return objective, that is a new ADR and a much longer time horizon.
- Reversibility: cheap on paper, but changing the objective after results are in is exactly the behavior the research warns about (§10, Behavioral). Any amendment must cite results that were not the reason for the change.
- Revisit if: the LLM layer enters scope (calibration criteria become primary for it); the owner's time or money situation changes materially.

## Amendment 2026-10-09: Phase 4 residual tracking (T70, #294)

**Status: Accepted by the owner's merge of PR #297 (#247 Q4, decided 2026-09-27; drafted 2026-09-27, completed 2026-10-09 once Probe 3 reported).** This and the matching [ADR 0006 amendment](0006-universe-and-cadence.md#amendment-2026-10-09-phase-4-paper-execution-t70-294) are one amendment, merged before `paper start` (T71). The reason for it is evidence gathered before any Phase 4 result exists, as the reversibility clause above asks: the execution and dividend mismatch documented in [#86's report](../research/2026-09-25-alpaca-open-and-depth.md) and measured by [Probe 3 (#182)](../research/2026-10-09-probe3-preopen-fills.md), never a Phase 4 return.

**The rule.** For Phase 4, success criterion 1 is checked on `paper.tracking_rule = residual`: `residual = raw + dividend term + fill-timing term`, with the terms and the monthly boundaries defined in [paper-trading requirement 10](../specs/paper-trading.md). The raw difference is paper return minus backtest return. The dividend term adds back the dividends the backtest reinvested and paper did not receive; the fill-timing term adds back the execution-price difference against the frozen `execution.fill_price` bar price (positive for a buy above, or a sell below, that price). Both are computed from recorded rows, never fitted to make a month pass. **The raw series is always printed beside the residual** and its two terms; the residue term is printed beside them and is never part of the residual.

Each non-excluded month's absolute residual must be at most the window's frozen `paper.tracking_k` times that month's modelled cost per rebalance. **Months whose rebalance has a `missed` event or an override are excluded from the maximum and listed with the reason** (ADR 0005 as written excludes missed months only; this amendment adds overridden months, since an override is the owner's decision, not the system's tracking). Months with `skip_*` decisions are listed with the skipped names and stay in the check. At least `paper.min_rebalances` (default 6) scheduler-executed rebalances and as many compared, non-excluded months are still required; exclusions never lower that count. The rule is frozen into `paper_windows` at `paper start`; reports and checks read that record, never the live settings. The other three success criteria stand.

**What Probe 3 measured, and its limits.** On Alpaca's paper simulator, on two sessions (2026-10-05 and 2026-10-09) with two liquid names (KO, AAPL), a $5 notional fractional buy queued before the open filled at the listing exchange's official opening print within 2 s of 09:30 ET in 4 of 4 cases; a 1-share buy filled at the NBBO ask 17 to 118 s after the open, −9 to +35 bps from the official open (median +6 bps), in 4 of 4 cases; a 1-share OPG order expired unfilled in 4 of 4. So paper's fill-timing drift is real but small for these names (tens of basis points on a whole share, zero on a pre-open fractional), and it is exactly what the fill-timing term adds back. **The evidence is thin by design of the owner's choice: two sessions, not the protocol's five; four buy fills per order type; no sell was probed; paper, not live.** The direction of every outcome was the same in all four cases, so the rule rests on it; the magnitudes (the median and worst latency and gap) are indicative, not a distribution, and the report grades them INSUFFICIENT. The first paper window's own fills, across many names and both sides, are the larger sample this rule is checked against; if they show that the fill-timing term is systematically larger than the modelled cost, that is a finding to record, not a reason to change this rule after the fact.
