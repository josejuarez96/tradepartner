- `ops.page_data`'s marks read no longer depends on the trading calendar (#652)
### Fixed
- Execution: `ops.page_data`'s marks read now filters on `session == last_marked_session` directly instead of `after=previous_session(last_session)`, so it no longer depends on every mark's session being an XNYS trading session (#652)
