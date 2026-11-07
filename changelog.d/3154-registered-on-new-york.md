- family_registered_on returns the New York day of the first registration, so an evening registration with holdout.start the next day is forward (ADR 0016 point 4, #3154).
### Fixed
- lab: a family's registration day is the New York date, not the UTC date, so a registration after 19:00/20:00 ET keeps forward tracking (#3154).
