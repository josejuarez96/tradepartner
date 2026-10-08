- test_ops_page no longer flakes across a session boundary
### Added
- Freeze the ops module clock to a single import-time instant in the shared _app helper and derive the fixture's S-1 from it; add a regression test that simulates the boundary crossing
