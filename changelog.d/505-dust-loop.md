- A delisted/receipt forced exit dusted below the trading minimum is now blocked once closed dust or settled, so forced_exits stops re-deciding it every session (#505).
### Fixed
- fix(execution): a sub-minimum forced-exit remainder (dust-closed or settled) no longer gets a new forced-exit decision every session (#505)
