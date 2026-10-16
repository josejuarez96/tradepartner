- #1714 T190b: a pre-trading seed shares fact is no baseline
### Fixed
- Universe rule 7: a shares fact dated before the security's first trade -- the later of its first listing `valid_from` and its first session, both known at T -- is no baseline; the first fact on or after it anchors, so a tiny pre-trading seed (AMCR 13,001, CTVA 100, FOXA 1, LIN 25,000, PSKY 1,000, SW 100, VTRS 100; the 59 census month-ends) no longer makes every later true fact out of line (#1682, T190b).
