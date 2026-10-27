task: T280b (PR #2299)
- T280b: pure break module (kinds, identity, parts, item 14's cause table, size, cumulative materiality, cap, state) and six frozen risk.* break keys, back-filled for older windows; no caller yet (#2296).
### Added
- `execution.breaks`: pure classification of reconciliation breaks, and the frozen `risk.break_*`, `risk.max_open_breaks` and `risk.max_open_break_share` keys at the #2266 defaults (ADR 0025, T280b).
