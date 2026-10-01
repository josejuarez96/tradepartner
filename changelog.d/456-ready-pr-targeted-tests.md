- #456 ready_pr runs targeted local pytest (the test files a diff maps to, plus the docs-budget test always), the full suite when the mapping is unclear or with `--full-tests`; CI still runs the full suite
### Changed
- ready_pr: local pytest runs only the tests the diff maps to (importers of changed modules, changed tests, tree-wide static checks), falling back to the full suite for conftest, dependency, fixture or unmapped changes; `--full-tests` runs everything; CI is unchanged (#456)
