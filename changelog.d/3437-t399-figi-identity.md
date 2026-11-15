task: T399 (PR #3439)
- OpenFIGI identity cross-check (read-only health block, gate unset by default); CLI print is #3440
### Added
- Health: a read-only figi_identity block comparing the store's identity with stored OpenFIGI FIGI and share-class data, gated by openfigi.max_identity_disagreements (unset: reports only).
