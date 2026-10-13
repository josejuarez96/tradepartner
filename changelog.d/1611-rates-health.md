- T169e done: health reports a per-series rates block (4 warnings), 3 rates integrity rules in --check, fred in last_ingests, page block and README carry the FRED attribution.
### Added
- Health and the health page report the stored FRED rates per series, warn on revisions, late rows, gaps and implausible values, and fail --check on bad rate stamps, duplicates or non-finite values.
