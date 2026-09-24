# 0001. Python with uv, ruff, mypy, and pytest

**Status:** Accepted  ·  **Date:** 2026-09-24  ·  **Issue:** #1

## Context
TradePartner is data- and research-heavy: time series, SEC filings, backtests, and possibly LLM calls. The owner's prior projects used Python. The quant ecosystem is Python-first (pandas/polars, DuckDB, edgartools, exchange_calendars, vectorbt, broker SDKs).

## Options considered
1. **Python + uv**: fast, lockfile-based, manages Python versions. It's the current standard.
2. **Python + pip/venv + requirements.txt** (as in AWT): familiar, but has no lockfile, gives unreproducible installs, and needs manual Python management.
3. **TypeScript**: weaker quant and data ecosystem for this use case.

## Decision
Python 3.12 (pinned in `.python-version`), managed by **uv** with `uv.lock` committed. **ruff** for lint and format, **mypy --strict** on `src/`, **pytest**. Package code in a `src/tradepartner/` layout.

## Consequences
- Good: reproducible installs (`uv sync --locked` in CI). One tool for venv, deps and Python version. Strict typing catches bugs early in financial code.
- Bad: mypy strict adds friction with untyped third-party libraries. Handle this with per-module overrides, not by disabling strict.
- Reversibility: cheap.
- Revisit if: a core dependency requires Python >3.12 features, or strict typing costs more than it catches.
