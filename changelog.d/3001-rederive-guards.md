- archive rederive: null fetch refused (bulk pre-T348 zips excepted), downloads outside the store lock, writes need --expect-digest (#3001)
### Fixed
- `archive rederive` refuses an entry with no fetch time unless it is a bulk entry whose listed zip has none, downloads its inputs with no store connection open and re-matches under the write lock, and a write needs the dry run's `--expect-digest` beside `--expect` (#3001).
