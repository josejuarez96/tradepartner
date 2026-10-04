- #691: execution/window.py and store/journal.py read schema.ENGAGE_KILL_SWITCH_KIND instead of the engage_kill_switch literal; behaviour unchanged, pinned by tests.
### Changed
- The window and journal readers compare override kinds against `schema.ENGAGE_KILL_SWITCH_KIND` rather than a copied `"engage_kill_switch"` literal (#691).
