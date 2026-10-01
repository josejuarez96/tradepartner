- #417/#402 Alerts: the scrubber uses config.secret_values (a blank secret no longer masks every space); a failed osascript records its scrubbed stderr
### Fixed
- A whitespace-only secret made the alert scrubber mask ordinary text; a failed osascript's delivery error omitted its stderr (#417, #402)
