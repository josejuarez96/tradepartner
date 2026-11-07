- #3190: a filename class conflict fails its CIK closed even with no pre-cover 10-K, so the Form 25 fallback no longer writes from the latest cover's class map; BUILD_RULES_VERSION 8.
### Fixed
- Filename listings: a CIK whose readings name one ticker for two classes now writes no Form 25 `filing_name` row when it has no pre-cover 10-K reading (#3190).
