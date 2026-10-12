- #1574 Universe rule 2: a valid_from tie between two exchanges is broken toward a universe venue
### Fixed
- Universe rule 2: a class filed on two exchanges with one valid_from is read on a venue in universe.exchanges instead of whichever exchange name sorts first, so a live NYSE/Nasdaq common is no longer excluded as exchange:<other> (#1574).
