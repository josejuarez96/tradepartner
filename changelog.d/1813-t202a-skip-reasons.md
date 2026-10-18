- T202a (#1813): store version 29 widens decision_events.reason (skip_asset_unknown, skip_not_fractionable) and order_events.reason (those two and skip_untradable); PR #1818.
### Changed
- Store schema version 29: the decision and order event reason CHECKs gain the allowlist's skip reasons, both tables rebuilt with every row kept (#1813, T202a).
