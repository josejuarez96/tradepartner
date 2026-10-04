- Replaced override_page's hardcoded engage_kill_switch literal with schema.ENGAGE_KILL_SWITCH_KIND, used in JOURNAL_ENUMS too (#636).
### Changed
- fix(dashboard): override_page's duplicate-guard exemption now references the shared schema.ENGAGE_KILL_SWITCH_KIND constant instead of a hardcoded literal (#636)
