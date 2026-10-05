- #943: the #847 stopped-line cut no longer drops a line that traded past its Form 25 (Seagate's 2016-2026 bars kept); alpaca.accepted_relistings lets the owner keep genuine long-gap relistings (MiMedx).
### Added
- `alpaca.accepted_relistings`: owner-listed security ids exempt from the #847 stopped-line cut, counted on the run row; a malformed id refuses the config (#943).
### Fixed
- Resolver: a delisted listing with a bar on or after its effective day is never a stopped line, so a store bar hole no longer cuts a live name (Seagate, #943).
