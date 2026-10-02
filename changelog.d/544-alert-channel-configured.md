- Fixed: alerts.channels=[store, email] with ALERT_* unset now refuses at config load, naming the missing variable (#544).
### Fixed
- config: Settings refuses a channels=[store, email] (or email-only non-store) config when the ALERT_* email settings are not fully set, naming the missing variable names; macos alone still satisfies the #366 Q22 (iii) usable-channel rule (#544).
