- paper check no longer crashes when a carried-residue name has a fill and a fully reversed quantity true-up in the same period (#3210).
### Fixed
- `paper check`: a quantity true-up reversed inside the same period no longer counts as a quantity-bearing row, so a fill beside it no longer raises in `compare_periods` (#3210).
