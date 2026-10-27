- paper: a spin-off's parent reference is re-keyed along a live successor chain (#2127)
### Fixed
- `store.asof.live_actions_as_of` now re-keys a `spinoff` row's parent (`source_action_id`, a security id) along a live successor chain like its `security_id`, so `reconcile_run._spinoffs` (and #2123's fill band) finds the held parent under the successor's id (#2127)
