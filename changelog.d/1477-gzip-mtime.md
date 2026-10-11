- #1477 fixed: test_llm_boundary's collection-time gzip value uses mtime=0 plus explicit ids, so xdist workers collect identical test ids.
### Fixed
- test_llm_boundary: a collection-time gzip.compress embedded the current mtime, so xdist workers could collect different test ids; now mtime=0 and explicit ids (#1477).
