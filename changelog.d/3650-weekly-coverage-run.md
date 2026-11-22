- #3650: weekly branch-coverage workflow on main (schedule and manual only, no gate) with per-package numbers in the job summary; pytest-cov added to the dev group.
### Added
- CI: `.github/workflows/coverage.yml` runs the suite under branch coverage every Sunday (or by hand) and writes a per-package table (`scripts/coverage_by_package.py`) to the job summary; no threshold and no PR check. New dev dependency `pytest-cov` (#3650).
