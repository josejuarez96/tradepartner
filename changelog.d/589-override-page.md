- T69b override page merged: the dashboard's only write, a form whose on_click submit calls T64b's override writer before the shell opens its read connection
### Added
- Dashboard override page (T69b): kind, rebalance session, name and reason go through T64b's `override` writer from an `on_click` callback that runs before the shell's read-only connection; refusals, `no_window` and store busy are shown, nothing written
