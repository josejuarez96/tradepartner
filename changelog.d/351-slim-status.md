- #351 STATUS cut to a board with the last 10 done lines; one fragment per PR; fold trims; CI token budgets on STATUS (2k) and CLAUDE.md (3k)
### Added
- Process: `tests/test_docs_budget.py` fails CI when `docs/STATUS.md` exceeds 2,000 tokens or `CLAUDE.md` 3,000 (#351)
### Changed
- Process: one bookkeeping fragment per PR (`changelog.d/<issue>-<slug>.md` holds the STATUS line and the CHANGELOG bullets); `fragments.py fold` keeps the last 10 STATUS "Recently done" lines; STATUS is a board (about 1.4k tokens, from 10k); the old `docs/status.d/` layout is still read during the transition (#351)
