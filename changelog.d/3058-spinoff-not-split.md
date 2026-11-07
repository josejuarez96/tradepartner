- #3058: a spin-off is no longer stored as a split (echo rule + reviewed list of 33); repair-spinoff-splits and the per-symbol spinoff-backfill wait for the data sitting.
### Added
- `repair-spinoff-splits` deletes the reviewed spin-off split rows under a data release, and `spinoff-backfill` asks Alpaca's per-symbol spin_offs for the months before the actions floor (#3058).
### Fixed
- Ingest refuses an Alpaca split row that is a spin-off: one on the ex-date of its parent's own spin-off in the same answer, or one of the 33 reviewed in store/spinoff_splits.csv (#3058).
