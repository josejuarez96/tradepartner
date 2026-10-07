- T100 (#1141): `paper start` refuses `refused_cadence` a hypothesis whose frozen_values cadence is not month_end, before any broker call; pre-lab registrations read as month_end; execution/ reads params through frozen_values.
### Added
- `paper start` refuses a hypothesis whose rebalance cadence is not `month_end` (`refused_cadence`, strategy-lab spec req 11) before any broker call.
