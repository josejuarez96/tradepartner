- test now routes _derived through run's loader; run calls window_journal_inputs for step 4/7b/_locked_run/_skipped
### Changed
- tests/lookahead/test_paper_invariance.py _derived uses window_journal_inputs on cut store; src/tradepartner/execution/run.py exposes WindowJournalInputs and window_journal_inputs; run's own reads (Tracking._executed, _locked_run, _skipped) now use it; _exit_book excluded per docstring
