- #2927 (PR #2928): build_master groups the filing index and its own rows once per build, not per CIK; the T184h filename-listings run (~13 h in the per-CIK index filter) is fixed, with the same shape in delistings and classify.
### Fixed
- The security-master build with filename listings on no longer scans the whole filing index once per issuer CIK (about 13 h per run); per-CIK and per-Form 25 reads of the build's own rows are grouped once too (#2927).
