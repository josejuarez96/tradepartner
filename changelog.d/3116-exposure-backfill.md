- T359 exposure backfill tool + reviewed manifest (11 launches, 8 candidates) ready for the 11-09 sitting; dry run on the real store: profitability N 1 -> 3 (#3116)
### Added
- `tradepartner exposure backfill --manifest --release [--dry-run]`: records the reviewed, hash-pinned manifest docs/research/exposure-backfill/manifest.toml (candidate 5 x2 store, CBOperProf x2, issuance x2, I1, XFIN, composite, cfp, #3092) through `record_exposure`, all or nothing under the open data release it names; the dry run shows every computed row and each family's N; a rerun adds no row (#3116, T359)
