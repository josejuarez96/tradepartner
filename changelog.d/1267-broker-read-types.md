- #1267 Broker read-side types (ADR 0015 seam 3, T135)
### Added
- adapters: Order, Fill, Account and Asset carry the read-side fields Alpaca returns (order shape, fee, margin, borrow flags), all at their defaults; OrderRequest is unchanged (#1267)
