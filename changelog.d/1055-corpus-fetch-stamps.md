- Fixed T120's blank-acceptance abort (edgar.acceptance_times skips a blank acceptanceDateTime) and amendments now carries each attachment's accepted_at (#1133).
### Fixed
- Corpus fetch: a blank acceptanceDateTime no longer aborts the departure-reason fetch; amendments carry accepted_at.
