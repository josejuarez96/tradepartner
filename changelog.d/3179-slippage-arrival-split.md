- #3179 (PR #3185): paper slippage splits each order's cost at its journaled submit time into open->arrival and arrival->fill; arrival priced only from the daily open (pre-open submits), else counted as no arrival price (#3184)
### Added
- Paper slippage (report, status, `paper cost`) prints each group's cost split at arrival into open->arrival and arrival->fill; totals unchanged; orders submitted after the open count as no arrival price (#3179).
