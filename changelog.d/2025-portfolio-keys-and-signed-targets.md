- T219: the frozen `portfolio` section and the pure `signed_target_weights` land, guarded until T223 (spec amendment #1872, items 1 and 2)
### Added
- T219: `PortfolioConfig` (`portfolio.side`, `gross_exposure`, `net_exposure`, `short_min_price`, `short_cover_weight`; the #1788 guard refusing any non-default until T223), its five frozen table entries and `POST_REGISTRATION_PORTFOLIO_KEYS`, `_FINGERPRINT_KEYS` membership, `FROZEN_SECTIONS`/`required_keys`/`frozen_params` wiring, and the pure `backtest.portfolio.signed_target_weights` (spec amendment #1872, items 1 and 2).
