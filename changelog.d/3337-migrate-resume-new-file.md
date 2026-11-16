- Raw archive migration resumes when a new move-class file appears after eviction, recording its tombstone and closing the journal (#3337).
### Fixed
- Raw archive migration retains newly settled move files after an eviction record; files arriving after its manifest wait for the next run (#3337).
