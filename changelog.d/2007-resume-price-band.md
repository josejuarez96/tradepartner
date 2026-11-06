### Added
- Paper resume checks fill prices against the configured band, keeps the kill switch engaged on a bad fill, and shows collected prices; any halt read finding a bad fill engages the switch, including a stale-data halt, and later resumes refuse (#2007, #2666).
