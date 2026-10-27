- T170d code half (#2011): metrics.risk_free_source defaults to store; the three risk-free keys leave every canonical set whatever their value (owner decision (a)), so no pin moves; the backfill and H.15 check are the owner's before merge.
### Changed
- metrics.risk_free_source now defaults to store (T170d, #2011); the risk-free keys never enter a canonical frozen set or fingerprint, so H1, B3 and the TWIN keep their pins.
