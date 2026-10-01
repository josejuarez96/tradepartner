- #395 A full exit of a name that lost `fractionable` sells the whole holding by its decision's flag (no `whole_shares` halt); trims and buys go whole-share; spec req 3 reworded (owner answer)
### Fixed
- Execution: `check_phase` no longer halts a full exit with `whole_shares` when the name lost `fractionable`; spec req 3's whole-share wording matches the owner's rule (#395)
