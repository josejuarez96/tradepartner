- #822 health: no_bars_after_delisting fails only bars resuming after a gap past effective_on; non_overlapping_listings skips NONE/OTC successors and same-day exchange-tag twins. Store: 31,867→5,004 bars, 55→21 overlaps.
### Fixed
- health: `no_bars_after_delisting` treats a listing's end as its last bar and fails only bars that resume after more than `master.transfer_window_sessions` missing sessions past `effective_on`; `non_overlapping_listings` no longer counts a NONE/OTC successor row or a same-day row of the same ticker differing only in exchange tag as an overlap (#822).
