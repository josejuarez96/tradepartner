# 0004. Tooling: what we own, what we adopt, what we avoid

**Status:** Accepted  ·  **Date:** 2026-09-24  ·  **Issue:** #5

## Context

The [handoff](../research/2026-09-24-initial-research-handoff.md) (D4) listed open-source tools without choosing. The owner wants the system's own logic to be solid and not overly dependent on third-party frameworks. A survey of the candidate repositories was run on 2026-09-24 via the GitHub API; the raw output is committed at [docs/research/2026-09-24-tooling-survey.md](../research/2026-09-24-tooling-survey.md) and the figures below come from it.

The MVP ([roadmap](../roadmap.md)) is a long-only, monthly-rebalanced, cross-sectional strategy on a daily-bar universe (ADR 0006). That is a small problem computationally. The pressure is on correctness and auditability, not speed.

## Options considered

**Backtesting engine**
1. **Adopt a full engine** (QuantConnect LEAN, NautilusTrader, zipline-reloaded, backtrader). Pro: mature. Con: LEAN and Nautilus are production engines for a different scale; zipline-reloaded is quiet (last release 2025-07, push 2026-01) with a data-bundle system that fights our own store; backtrader is GPL and dormant (last push 2024-08).
2. **Adopt a vectorized engine** (vectorbt, Apache 2.0 + Commons Clause, release 2026-07). Pro: fast parameter sweeps. Con: sweeping thousands of parameters is the behavior our trial-counting rule exists to discourage; the abstractions hide the mechanics the owner is trying to learn.
3. **Adopt `bt`** (MIT, v1.2.3 released 2026-09-11, 14 open issues). Pro: its design (`RunMonthly → Select → Weigh → Rebalance`, with cost models) matches the MVP exactly, and it is small enough to read end to end. Con: it is one more dependency in the core.
4. **Write a thin engine, use `bt` as an oracle** (this ADR). Pro: the owner understands every line; the engine reads directly from our point-in-time store and writes directly to the trial registry; `bt` independently checks the result. Con: a few hundred lines to write and maintain.

**Storage**
1. **SQLite.** Pro: ubiquitous, transactional. Con: row-oriented; slow for the columnar scans (all tickers × all dates) that signals and backtests do.
2. **Parquet files only, DuckDB as a query layer.** Pro: simple files, easy to back up and version. Con: no transactions or in-place updates; appending `ingested_at` corrections means rewriting partitions.
3. **DuckDB database file as the store, Parquet for exports and fixtures** (this ADR). Pro: columnar and fast for our access pattern; single file; SQL for the dashboard; transactions for ingestion. Con: single-writer; the file format has changed across major DuckDB versions, so pin and export to Parquet at phase tags.

**Metrics.** Writing Sharpe, drawdown and turnover by hand invites bugs; `quantstats` (Apache, push 2026-07) and `empyrical-reloaded` (Apache, push 2025-12) are standard. Deflated Sharpe and PBO are not in either and are small enough to write and test ourselves.

**Data platforms.** OpenBB (AGPL-3.0) as a core dependency would put the whole project under AGPL. Qlib (MIT) is an ML platform whose point-in-time design is worth reading but whose platform is far heavier than needed.

**LLM trading frameworks.** TradingAgents (Apache, 108k stars) and ai-hedge-fund (MIT, 64k stars) are built around an LLM acting as the trader, which development-process rules 4 and 5 forbid, and their published backtests are contaminated by construction (handoff §8.4).

## Decision

**We own** (in `src/tradepartner/`): the point-in-time store schema, the security master, the calendar wrapper, the trial registry, the decision journal, the cost model, signals, risk rules, the backtest engine, and the dashboard pages.

**We adopt:**

| Package | License | Role |
|---|---|---|
| `duckdb` | MIT | the store (single database file); version pinned; Parquet export at every phase tag |
| `pyarrow` | Apache | Parquet read/write |
| `polars` | MIT | dataframes (pandas allowed at library boundaries that require it) |
| `exchange_calendars` | Apache | trading calendar (XNYS) |
| `edgartools` | MIT | SEC EDGAR: Form 4, XBRL, Form 25. Pin the version; one maintainer |
| `alpaca-py` | Apache | free market data and paper/live broker adapters |
| `empyrical-reloaded` or `quantstats` | Apache | standard performance metrics (choose one in the Phase 3 plan) |
| `bt` | MIT | **test-only** dependency: oracle for the backtest engine |
| `pydantic` | MIT | config and record schemas |
| `streamlit` or `marimo` | Apache | dashboard (choose one in the Phase 2 plan) |

**We avoid:** backtrader, backtesting.py (AGPL, single-asset), vectorbt (for the MVP), LEAN, NautilusTrader, OpenBB in the core, TradingAgents and ai-hedge-fund as dependencies (reading them for ideas is fine), and `yfinance` outside prototyping and spikes.

### Engine oracle test

The first registered hypothesis (provisionally 12-1 momentum) runs through both our engine and `bt` on the fixture universe, and the two must agree. To make that a fair comparison:
- both engines use **zero costs, fractional shares** (`integer_positions=False` in `bt`), and the **same lagged signal** (computed from session T's close, filled at the **official open of session T+1**, per ADR 0003 rule 2 and ADR 0006);
- both receive the **same adjusted-as-of-T price frame**, produced by our store; `bt` never sees raw prices or corporate actions directly;
- the delisted fixture name is **sold at its last available price on its final session** in both engines; the test asserts this explicitly;
- the measure is the **maximum absolute relative difference in portfolio equity across all sessions**, and the tolerance is **1e-9**. The tolerance is a constant in the test file, not runtime config; changing it needs review in the PR;
- the **cost model is tested separately** against hand-computed expected values on a three-trade fixture, because `bt` and our engine model costs differently.

Oracle and fixture runs are logged in the trial registry with a `synthetic = true` flag and excluded from the trial count, so domain rule 2 (count every trial) is satisfied without inflating the count with test runs.

## Consequences

- Good: a small, permissive-license dependency set; every number that matters is produced by code the owner can read; an independent check on the engine; storage chosen with the access pattern in mind.
- Bad / accepted risks: we maintain our own engine. If the strategy later needs intraday bars, shorting, or event-driven fills, that engine will not be enough and this ADR should be superseded. DuckDB is single-writer, so ingestion and the dashboard must not write concurrently (the dashboard is read-only by design, see roadmap).
- Reversibility: cheap for adopted libraries; costly for the store schema (which is why it is ours) and moderately costly for the DuckDB choice once data is loaded (mitigated by Parquet exports).
- Revisit if: the strategy scope leaves daily-bar, long-only rebalancing; a dependency changes license or goes unmaintained for 12 months; or the survey ages past a phase retro without being refreshed.
