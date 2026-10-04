- #719 (#647 follow-ups): open sells count off-grid estimates up, held sells are logged and reported in PhaseOrders.held (buy guard sees them), spec acceptance for the trim cap and the DAY-order clause.
### Fixed
- Open own sells are subtracted exactly and an off-grid unfilled quantity now counts up to the quantity grid (nearest step only within float noise); a held sell logs one warning line and blocks a same-phase buy of the name (#719).
