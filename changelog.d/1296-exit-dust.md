- #1296 fixed: the ledger sums positions in Decimal, so a full exit at quantity_decimals=9 sells the whole holding and leaves no 1e-9 share.
### Fixed
- A full exit at 9 quantity decimals no longer leaves one nano-share open: the ledger sums fills in Decimal instead of float (#1296).
