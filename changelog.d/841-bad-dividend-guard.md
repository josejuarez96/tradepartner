- #841: a dividend at or above adjust.max_dividend_to_prior_close (default 1.0) of its prior close is dropped and reported as implausible_amount, not raised; CG 2017-09-13 no longer aborts H1. New frozen adjust key: re-register H1.
### Fixed
- A dividend at or above `adjust.max_dividend_to_prior_close` times its prior close (bad source data, e.g. Alpaca's CG 2017-09-13 $25 on a $22 stock) is left unapplied and reported by `dropped_dividends_as_of` as `implausible_amount`, instead of failing every adjusted read and the whole backtest trial (#841).
