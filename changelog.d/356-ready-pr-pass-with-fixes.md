- #356 ready_pr's review gate counts only a latest `<agent>: PASS`; a `PASS WITH FIXES` now needs a re-review that posts `PASS` (agents, ready-pr skill and PR template say so)
### Fixed
- ready_pr: a `PASS WITH FIXES` verdict no longer passes the review gate while its findings may be open; the re-review after the fixes must post `PASS` (#356)
