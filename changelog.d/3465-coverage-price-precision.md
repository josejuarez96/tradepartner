- Release coverage reads a retracted bar held at 4 decimals as the receiver's bar cut to that precision (store.release_move_price_decimals, #3465): SNTI/JTAI 2026-11-05 count as vendor revisions
### Fixed
- decision data-release coverage: a lost or retracted bar whose prices sit on the store.release_move_price_decimals grid (default 4) matches a newer bar less than one unit of that last decimal away; a whole unit off is still another price (#3465)
