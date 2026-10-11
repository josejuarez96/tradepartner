- #410: a forced exit of a delisted name missing from the assets read keeps the fail-closed asset_missing halt (the Alpaca adapter raises, never omits); pinned by tests, inactive asset ends untradable (req 14)
### Changed
- Pinned #410: a forced exit with no asset read stays an `asset_missing` halt, an inactive asset ends `untradable`, and the Alpaca asset read raises on a 404 rather than omitting the symbol.
