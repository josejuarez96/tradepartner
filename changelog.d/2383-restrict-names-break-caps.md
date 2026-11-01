task: T282a (PR #2386)
- T282a: names with an open material break take no added exposure (skip_restricted), reducing orders are cut to min(ledger, broker), and the break cap halts the book; T281's halt stays until T282d
### Added
- Restricted names and the break cap behind the existing halt (T282a, ADR 0025)
