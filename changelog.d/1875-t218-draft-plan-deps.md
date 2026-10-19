- T218 drafted: the long/short spike's experiment file (owner registers and runs); the paper short build no longer waits for T225 (only T237 does); T171d un-paused (#1875).
### Added
- The T218 experiment file `docs/experiments/long-short-spike.md` (H1's ranking as a 50/50 book with a flat borrow fee, in-sample only), ready for the owner to register and run.
### Changed
- The paper short order-path tasks (T226, T228, T229, T231 to T234) start now, beside the backtest tasks; the first paper short book (T237) still waits for T225. T171d (borrow-status record) is un-paused so forward history accumulates.
