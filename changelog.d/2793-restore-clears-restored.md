- archive restore skips already-restored paths; a folder restore after a partial one works (#2793)
### Fixed
- archive restore: a folder restore after a partial restore skips restored paths and says so when nothing is left; a tombstone line with no path is skipped
