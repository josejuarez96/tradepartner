task: T287 (PR #2294)
- T287 (#2291): a no-cover-page SEC ticker rename is one dated snapshot listing row, idempotent across nightly runs, not a second same-start static row (PR #2294)
### Fixed
- The nightly EDGAR ingest no longer writes a second same-start listing when the SEC renames a security with no cover page; the rename is a snapshot row from the fetch session (#2291).
