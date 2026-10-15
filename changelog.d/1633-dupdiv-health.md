- T186b (#1633): `health` gains the `duplicate_dividends` rule: unlisted equal-amount cash pairs and wrong identity counts fail; accepted and unresolved keys are listed; pending listed duplicates are a CLI warning, not a failure.
### Added
- `tradepartner health` reports duplicate cash dividends (`duplicate_dividends`): an equal-amount pair the reviewed list does not explain fails `--check`; listed pairs are shown, and pending repairs are a warning (#1613).
