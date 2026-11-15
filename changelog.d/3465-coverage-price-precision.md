- Release coverage reads a retracted bar held at 4 decimals as the receiver's bar cut to that precision (store.release_move_price_decimals, #3465): SNTI/JTAI 2026-11-05 count as vendor revisions
### Fixed
- decision data-release coverage: a lost or retracted bar whose prices sit on the store.release_move_price_decimals grid (default 4) matches a newer bar that truncates or rounds to it (the band [old - u/2, old + u) for u one unit of that decimal); a whole unit off is still another price (#3465)
