- Fill-price sanity band (#1981): a run halts with fill_price_outside_band when a fill is over paper.max_fill_price_deviation (0.25) from its reference close.
### Fixed
- A fill at 100x or 0.01x the reference close no longer passes silently: the run halts, named fill_price_outside_band, band from run-time paper.max_fill_price_deviation (default 0.25) (#1981).
