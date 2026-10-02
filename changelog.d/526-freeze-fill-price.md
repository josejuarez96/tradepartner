- #526: paper start freezes execution.fill_price into frozen_json (FROZEN_EXECUTION_KEYS) and refuses execution_drift when it differs from the registration, so paper report reads it on a real window instead of raising ValueError
### Fixed
- paper start now writes execution.fill_price into the window's frozen_json, refusing (execution_drift) when the live value differs from the hypothesis's registered one; paper report no longer raises ValueError on a window paper start created (#526)
