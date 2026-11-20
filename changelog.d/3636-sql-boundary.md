### Added
- tests/test_sql_boundary.py: a ratchet on .execute/.executemany call sites under src/tradepartner/ outside store/, against the per-file baseline tests/fixtures/sql_boundary_baseline.txt (406 sites in 46 files); a new site or a total above the baseline fails, and so does a baseline left higher than the scan (#3636).
