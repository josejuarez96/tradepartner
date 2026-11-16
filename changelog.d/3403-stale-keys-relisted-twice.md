- #3403: stale-keys distinguishes a kept successor from a later relisting of the same CIK.
### Fixed
- stale-keys flags kept successors only when their id is missing or another id holds the same listing span; superseded hits require owner review (#3403).
