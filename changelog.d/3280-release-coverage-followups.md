- Release coverage now refuses aggregate lost sessions and incomplete backups with named errors (#3280).
### Fixed
- Release coverage fails closed when the configured store-wide unmoved-session total is exceeded, including small losses that previously passed; it names missing backup tables, documents the sub-dollar price floor, and tests secret scrubbing (#3280).
