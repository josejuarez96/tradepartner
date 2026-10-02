- #534 the wrapper sizes buys and checks the cash rule with the window's frozen costs.* (paper start freezes per_side_bps and the two commissions); T60b review follow-ups 1, 4, 5 and 6
### Changed
- paper start freezes costs.per_side_bps, costs.commission_per_share and costs.commission_per_order into frozen_json, and the wrapper reads its costs from the window, never live settings; a window without them trades nothing (#534)
