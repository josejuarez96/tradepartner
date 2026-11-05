- Restamp keeps moved facts an earlier ingest than a stored revision, so as-of reads after the revision no longer change (#2970, PR #2985).
### Fixed
- A restamp no longer makes `facts_as_of` serve a pre-revision date set again between a stored revision and the restamp run (#2970).
