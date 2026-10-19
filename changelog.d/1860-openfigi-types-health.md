- #1713 T189c: the OpenFIGI `figi_types` health block and the check-only tests close the task
### Added
- Health: the `figi_types` block names a class the store calls `common` that OpenFIGI calls a note and the converse, reports the unmapped count, and gates on `openfigi.max_disagreements` when set (#1713).
- Tests: the check-only source scan now covers `openfigi_types`, and universe, backtest and paper runs are byte-identical with the table populated and empty (#1713).
