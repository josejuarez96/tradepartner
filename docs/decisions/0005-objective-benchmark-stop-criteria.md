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

## Amendment draft — 2026-09-27: Phase 4 residual tracking (T70, #294)

**Status: Proposed; blocked on Probe 3's report (#182).** This and the matching
[ADR 0006 amendment](0006-universe-and-cadence.md#amendment-draft--2026-09-27-phase-4-paper-execution-t70-294)
are one amendment, to merge together before `paper start` (T71). The owner chose
this rule in [#247 Q4](https://github.com/josejuarez96/tradepartner/issues/247#issuecomment-5851267262).
It does not become an accepted amendment or complete T70 until the measured
report is cited and the six timing defaults and their tests are finalized.

For Phase 4, the first success criterion uses `paper.tracking_rule = residual`:
`residual = raw + dividend term + fill-timing term`, with the terms and monthly
boundaries defined in [paper-trading requirement 10](../specs/paper-trading.md).
The raw difference is paper return minus backtest return. The dividend term adds
back reinvested backtest dividends paper did not receive; the fill-timing term
adds back the execution-price difference (positive for a buy above, or a sell
below, the frozen bar price). These are computed from the recorded data, never
fitted to make a month pass. The raw series is always printed beside the residual
and its two attribution terms. The residue contribution is reported separately
and is never subtracted from the residual.

Each non-excluded month's absolute residual must be at most the window's frozen
`paper.tracking_k` times that month's modelled cost per rebalance. Months whose
rebalance has a `missed` event or an override are excluded from the maximum and
listed with the reason. Months with `skip_*` decisions are listed with the skipped
names and remain in the check. At least the frozen `paper.min_rebalances` (default
6) scheduler-executed rebalances and as many compared, non-excluded months are
required by the spec's exit check. Exclusions do not lower that requirement.

The rule is frozen into `paper_windows` at `paper start`; reports and checks use
that record, never the current settings. The other three success criteria stand.
The reason for this amendment is the execution and dividend mismatch documented
in [#86's report](../research/2026-09-25-alpaca-open-and-depth.md), rather than a
Phase 4 return result. [Probe 3 (#182)](https://github.com/josejuarez96/tradepartner/issues/182)
is still pending: its measured latency and fill-price gaps must be added as
evidence before this amendment is accepted. Paper evidence characterizes the
simulator, not live execution.
