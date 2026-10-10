- T155b (#1403, PR #1404): paper run drives every open book in token order, each under its own lock and paper_runs row, a crash in one never stopping the next; --book on the paper commands and paper kill --all; H1 (main) pinned unchanged.
### Added
- `paper run` with no `--book` runs every book with an open window, each under its own lock; `--book` on `start`, `stop`, `run`, `reconcile`, `kill`, `resume`, `abandon`, `override` and `settle`, and `paper kill --all` (ADR 0017 B.3, T155b).
