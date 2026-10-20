- T245 `tradepartner index-check` (#1598): the store reads, flags and tables over T243/T244; protected months gaps only; never a trial, writes nothing; T246 (owner) runs it on the real store.
### Added
- `tradepartner index-check [--start] [--end] [--store] [--out]` and `backtest/index_check_store.py` (#1598, T245): the whole universe, equal and cap weight, against RSP and SPY by year and month on read-only connections, with per-name flags; months after the protected edge show gaps only. Never a trial; writes nothing.
