- #3675 store backup claims its minute atomically (exclusive partial manifest); a second run in the same minute is refused and cleanup removes only its own files; in review
### Fixed
- Store backup: a run claims its target atomically by creating its partial manifest exclusively, so two backups in the same minute can no longer overwrite each other, and a failed run removes only the files it created (#3675).
