- #816: Alpaca corporate-actions batches drop an action an earlier batch returned identically (GMGI->MRDN reverse split came back in both batches); differing records with one id still fail closed. Unblocks the 2026-03+ backfill.
### Fixed
- Alpaca corporate actions: an action returned identically by two symbol batches (e.g. a split naming old and new symbol) is kept once instead of failing the backfill; differing records with one id still fail (#816).
