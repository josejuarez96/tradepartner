- Data health releases the store after each read and preserves the fixture view (#2472).
### Added
- Data health uses short read-only connections for each query (#2472).
### Fixed
- Data health no longer holds one store read connection across Python aggregation and page drawing (#2472).
