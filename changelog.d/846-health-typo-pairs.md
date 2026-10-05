- health non_overlapping_listings now shares the resolver's same-day-typo rule and an Alpaca-symbol spelling fold, so FF/TMPM/CLRC/MOTV U-MOTV.U no longer fail --check (#846); HACAR/HCACR split to #855.
### Fixed
- health: non_overlapping_listings shares ListingResolver's held-ticker same-day-typo rule (extracted as is_same_day_typo) and a new same_alpaca_symbol space/dot-suffix fold, so FF/F, TMPM/TMPMW, CLCR/CLRC and MOTV U/MOTV.U no longer fail the integrity check (#846).
