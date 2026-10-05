- #953 fixed: CI xdist worker segfaults came from DuckDB worker-thread teardown in its bundled jemalloc; tests now open DuckDB with threads=1 (tests/conftest.py), tests/execution runs ~2x faster.
### Fixed
- Tests open every DuckDB database with `threads=1`, so DuckDB's bundled jemalloc never tears down worker threads, the path that intermittently segfaulted CI's xdist workers (#953).
