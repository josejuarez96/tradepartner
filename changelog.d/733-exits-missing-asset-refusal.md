- exits.py: a held name missing from the assets read raises a named MissingAssetRefused (a ValueError), not a bare ValueError (#733).
### Fixed
- execution: `exits._asset` raises `MissingAssetRefused` (a `ValueError`) instead of a bare `ValueError` when a held name is missing from the assets read; the run still fails closed (#733).
