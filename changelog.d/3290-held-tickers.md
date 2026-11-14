- orphan test (_held_tickers) reads the id's own withdrawn tickers, case-normalised: G5 false orphans become moved (#3290)
### Added
- repair-resolution: the orphan count no longer counts bars whose receiver sits under the id's own retracted ticker (warrant-row and lowercase cases)
