- #1498 (PR #1499): ADR 0020, FRED/ALFRED DTB3 as a stored point-in-time T-bill rate; data-foundation `rates` table and known_at rule; metrics read a per-period risk-free series (constant kept, no trial moves); plan T169-T170d.
### Added
- ADR 0020 and the data-foundation, backtest and strategy-lab spec amendments for a stored point-in-time 3-month T-bill rate (FRED/ALFRED `DTB3`): the `rates` table and its `known_at` rule, the FRED adapter and ingest, a per-period risk-free series in the Sharpe behind frozen `metrics.risk_free_*` keys with the constant as the fallback, and plan tasks T169 to T170d (#1498).
### Changed
- `.env.example` documents `FRED_API_KEY` (#1498).
