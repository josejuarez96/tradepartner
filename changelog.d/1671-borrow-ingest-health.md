- T171c+T171e (#1671, #1672): borrow-status snapshot ingest (ingest --source alpaca_assets, --import-dir, raw keep), BorrowConfig (off by default), health borrow block, page card and borrow_status_unique check.
### Added
- Borrow-status snapshot: `ingest --source alpaca_assets` (one unfiltered GET /v2/assets, last under `--source all` once `borrow.enabled` is on), `--import-dir` for the recorder's files, the raw keep under `borrow.raw_dir`, and the health borrow block, page card and `borrow_status_unique` check (#1506, T171c, T171e).
