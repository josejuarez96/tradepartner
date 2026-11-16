- health --check back to ~22 s on a real-store copy from ~11 min: the shares cap-jump query keeps DuckDB's ASOF join plan instead of a nested loop (#3232)
### Fixed
- `health --check` runs in about 22 s again, down from about 11 minutes: the shares cap-jump list's ASOF join no longer falls back to a nested loop over every bar (#3232).
