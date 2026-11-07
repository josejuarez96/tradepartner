- #3032: the EDGAR facts/statement caches carry a fail-closed key sidecar, so the nightly cache-completeness probe reads each key without parsing the payload (full parse on any mismatch).
### Changed
- EDGAR ingest's nightly cache-completeness probe reads each per-CIK facts/statement cache key from a `<cik>.json.key` sidecar pinned to the file's size, mtime and inode, falling back to a full parse (and healing the sidecar) whenever it is absent or does not match (#3032).
