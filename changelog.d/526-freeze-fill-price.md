- #526: paper start freezes execution.fill_price into frozen_json (FROZEN_EXECUTION_KEYS), so paper report reads it on a real window instead of raising ValueError
### Fixed
- paper start now writes execution.fill_price into the window's frozen_json; paper report no longer raises ValueError on a window paper start created (#526)
