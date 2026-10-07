- fix(adapters): `edgar_source.reduce_submissions` skips a blank-acceptance accession instead of aborting (#1138)
### Fixed
- `edgar_source.reduce_submissions` no longer raises `KeyError`/`ValueError` on a blank `acceptanceDateTime`; `frame._match_delisting` resolves two same-second, same-exchange delistings of one CIK by `class_title` instead of raising
