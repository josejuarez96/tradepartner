task: T282d (PR #2466)
- T282d (PR #2466): a breaks reconciliation no longer halts the book; the name is restricted and the rest trades, break_cap is a reconciliation mismatch, resume/stop/settle/residues read a breaks base.
### Added
- A `breaks` reconciliation restricts its names and halts nothing; `break_cap` and a restricted name in a long_short book (#2465) still halt; `paper stop` refuses `open_breaks` while a material break remains.
