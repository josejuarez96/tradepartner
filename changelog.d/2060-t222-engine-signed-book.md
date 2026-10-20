- #2060 T222: the engine's signed book, short exits, borrow and free-cash accrual (PR #2069)
### Changed
- Backtest: `_step` marks, trades and exits a signed book under `portfolio.side = long_short`, paying the borrow fee every session held and accruing `rf(s)` on free cash only (spec amendment #1872, items 4 to 7); `long_only` runs unchanged (#2060)
