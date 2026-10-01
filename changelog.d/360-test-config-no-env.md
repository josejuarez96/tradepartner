- #360 `tests/test_config.py` isolates from the owner's `.env` (the no-env test points `TRADEPARTNER_ENV_FILE` at a missing file) and clears every Settings env var by field, so ready_pr's local pytest passes on the main checkout
### Fixed
- Tests: `test_config.py` no longer reads the project `.env` or a leaked `STORE__PATH`-style variable, which failed ready_pr's local pytest on checkouts with a real `.env` (#360)
