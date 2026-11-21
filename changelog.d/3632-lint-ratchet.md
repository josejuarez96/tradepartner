- T437: ruff now fails a new blind except, a print in src/ and a function over 25 complexity / 25 branches / 100 statements / 12 arguments; 142 existing lines in 52 files grandfathered by per-line noqa (#3632).
### Added
- Lint ratchet: ruff selects BLE001, T20 (src/ only), C901, PLR0912, PLR0913 and PLR0915 at limits measured at about the 99th percentile of src/; the 142 existing offending lines carry a per-line noqa written by `ruff check --add-noqa`, and tests/test_lint_ratchet.py pins the selection (#3632).
