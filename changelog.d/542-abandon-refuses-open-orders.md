- #542 owner decision: `window.abandon` refuses `open_orders` (nothing written, nothing cancelled) while any order of the window is non-terminal in the journal, stop's not_ready read; spec req 14 amended
### Changed
- `window.abandon` refuses with `WindowCommandRefused(open_orders)` before any broker call or write while an order of the window has no terminal event in the journal; it never cancels (#542)
