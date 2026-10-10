- #828 items 3-4: store.delistings imports _NOT_COMMON_WORDS/_is_plain_common from master (no copy); test pins one delistings() source call per EDGAR chunk. Items 1, 5 remain (owner).
### Changed
- store.delistings reuses master's plain-common-stock title check instead of a hand copy, and a test pins that the EDGAR chunk asks the filing source for Form 25s exactly once (#828).
