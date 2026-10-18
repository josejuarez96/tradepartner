- T212 (A11): universe.rank_by cap|dollar_volume (default cap, byte-identical, frozen default entry): median dollar volume from bars, no shares fact, Alpaca assets intersection skipped before first snapshot (#1743)
### Added
- universe.rank_by (cap, dollar_volume) for forward books: ranks by median dollar volume over the history window from bars alone, intersected with Alpaca tradable assets as of t (skipped and recorded before the first snapshot); cap stays the default and no fingerprint moves.
