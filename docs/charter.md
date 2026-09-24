# Charter: TradePartner

**Status:** ACCEPTED 2026-09-24 (issue #7). Changes happen only through an ADR. Items marked ⬜ are owner facts that gate a later phase, as noted.
**Source:** [initial research handoff](research/2026-09-24-initial-research-handoff.md), §2 and §12.

## Purpose
A personal, local system for researching, testing and paper-trading (then small-live) US equity strategies, with every decision logged. The main aim is to learn how to make disciplined, evidence-based decisions. Beating the market is a separate, harder, optional goal.

## Objective & success criteria ([ADR 0005](decisions/0005-objective-benchmark-stop-criteria.md))
- **Primary objective, ranked:** (1) disciplined, evidence-based decisions, shown by process metrics; (2) calibration, once anything emits probabilities; (3) beating a benchmark, optional and never a phase-exit criterion.
- **Benchmarks:** SPY and MTUM, total return, computed inside the system with the same data and cost model. Measured against, never targeted.
- **Success looks like:** the phase exit criteria in [roadmap.md](roadmap.md), plus at Phase 4 exit: paper tracks backtest within the configured tolerance; every order has a complete journal chain; every override has a reason; every backtest run is in the trial registry.
- **Stop / failure criteria:** no phase exit within 6 months of the last tag forces an explicit continue-or-stop decision at the retro; exceeding the budget halts paid ingestion; any reported result found to have look-ahead, or any untracked run, halts the phase. Live capital is never topped up without amending this charter.

## Scope
- **In:** US equities, long-only, low frequency (daily or slower), no leverage, no options trading. The owner may revisit this through an ADR.
- **Out:** the utility sector (owner compliance decision; SIC ranges are a guarded setting, proposed 4900–4949 and 4960–4999, changeable only by amending this charter). Anything using material non-public information.
- **Deferred beyond the MVP** (ADR required to enter scope): social and Google Trends data, news-text signals, and the LLM analyst layer. See [roadmap.md](roadmap.md), "MVP scope".
- **Interface:** the owner interacts through a CLI and a local, read-only dashboard that reads the system's own database. The only write action is a logged override with a reason. See [roadmap.md](roadmap.md), "User experience".
- **Universe** ([ADR 0006](decisions/0006-universe-and-cadence.md)): US common stocks on NYSE, Nasdaq and NYSE American; ranked by market cap with liquidity and price floors, rebuilt point-in-time at each rebalance. The numbers (proposed defaults: top 1000, $5M median dollar volume, $5 price) are config, frozen per hypothesis at pre-registration.
- **Cadence** ([ADR 0006](decisions/0006-universe-and-cadence.md)): monthly rebalance at the last session of the month, orders at the next open, one-month hold.

## Constraints
- Runs locally, for personal use only.
- Paper trading first. Live capital is about $100 at most until the charter is amended. ⬜ Account type (taxable or retirement): decide before Phase 6.
- ⬜ **Compliance:** employer personal-trading policy reviewed. Required before Phase 6; blocks nothing earlier.
- **Budget:** data-vendor spend is deferred to the start of Phase 3 per [ADR 0003](decisions/0003-data-adapters-local-first.md); interim ceiling for spend **by the running system** (data, APIs, LLM calls; development tooling excluded) is **$0/month** (free tiers only). The Phase 3 vendor ADR sets the ceiling for data, APIs and LLM spend from then on.
- **Time:** no fixed weekly hours. Build and review hours are recorded in each phase retro, and the 6-month stop criterion above applies. The owner reviews and merges every PR (~≤400 lines each).

## Principles (from the research)
1. Any edge comes from strategy, data or discipline. It does not come from using an LLM.
2. Hypothesis before data. Count every trial. Keep a locked holdout.
3. Code computes numbers. LLMs never place orders.
4. Point-in-time data, including delisted names.
5. Rules are set before money is at risk. Overrides are logged.
6. Around 100 trades is mostly noise. Tune on process (calibration, costs, bugs), not on P&L.
