- Execution: a long-to-short flip's close sells the whole holding, residue included (#1959); the buy phase re-checks skip_short_price via buy_orders(short_min_price=) (#1969). Paper spec amendment for req 3 and item 14 (e) is a separate PR.
### Added
- execution/phases: buy_orders short_min_price keyword re-checks skip_short_price (#1969)
### Changed
- execution/phases: a flip's close sells the residue too (#1959)
