- #1931 T211d: the data vintage reads the trial's `adjust.apply_spin_offs` (spec amendment #1894, item 6) (PR #1935)
### Added
- Data: `data_vintage(..., spin_offs=)` counts the stored `spinoff` rows a spin-off-reading trial sees, passed from the trial's frozen `adjust.apply_spin_offs`; at the default no stored vintage moves. (#1931)
