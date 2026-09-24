# Charter: TradePartner

**Status:** DRAFT. The owner must answer the ⬜ items before Phase 1 exits. After acceptance, changes happen only through an ADR.
**Source:** [initial research handoff](research/2026-09-24-initial-research-handoff.md), §2 and §12.

## Purpose
A personal, local system for researching, testing and paper-trading (then small-live) US equity strategies, with every decision logged. The main aim is to learn how to make disciplined, evidence-based decisions. Beating the market is a separate, harder, optional goal.

## Objective & success criteria
- ⬜ **Primary objective:** learning, calibration, or beating a benchmark? Rank them.
- ⬜ **Benchmark(s):** e.g. SPY buy-and-hold; MTUM for a momentum strategy.
- ⬜ **Success looks like** (measurable, by date):
- ⬜ **Stop / failure criteria** (set now, not after a drawdown): e.g. max drawdown, max months underperforming, max monthly spend on data and APIs, max hours per week.

## Scope
- **In:** US equities, long-only, low frequency (daily or slower), no leverage, no options trading. The owner may revisit this through an ADR.
- **Out:** the utility sector (owner decision). Anything using material non-public information.
- ⬜ **Universe:** market-cap and liquidity floor.
- ⬜ **Cadence:** rebalance frequency and holding periods.

## Constraints
- Runs locally, for personal use only.
- Paper trading first. Live capital is about $100 at most until the charter is amended. ⬜ Account type: taxable or retirement.
- ⬜ **Compliance:** employer personal-trading policy reviewed? (Required before any live trading.)
- ⬜ **Budget:** monthly ceiling for data, APIs and LLM spend.
- ⬜ **Time:** hours per week the owner can give this.

## Principles (from the research)
1. Any edge comes from strategy, data or discipline. It does not come from using an LLM.
2. Hypothesis before data. Count every trial. Keep a locked holdout.
3. Code computes numbers. LLMs never place orders.
4. Point-in-time data, including delisted names.
5. Rules are set before money is at risk. Overrides are logged.
6. Around 100 trades is mostly noise. Tune on process (calibration, costs, bugs), not on P&L.
