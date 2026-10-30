- EDGAR filing index is computed once per source instance (#2593).
### Fixed
- Cache the full stamped EDGAR filing index per source run; later calls preserve since filtering without re-reading or re-stamping (#2593).
