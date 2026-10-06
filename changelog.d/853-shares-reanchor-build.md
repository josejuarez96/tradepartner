- T117 (#853): rule 7 re-anchors a shares baseline once a run of agreeing rejected facts spans max_shares_age_days; H1's frozen universe keys pinned unchanged; trial 2 may follow S1/S3
### Fixed
- Universe rule 7: a mis-scaled first shares fact no longer excludes a name for good; a run of agreeing later facts spanning `universe.max_shares_age_days` re-anchors the baseline, and +inf is not a share count (#853, T117)
