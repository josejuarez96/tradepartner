- T84b done: window.settle_order, the owner settlement writer (req 17, #571): run lock, journal refusals, one read-only broker read, three-part gate with the reset exception; FakeBroker Reset/SetPosition. CLI is T84c.
### Added
- `window.settle_order`: the owner-only writer behind `paper settle` (spec req 17, #571), and the fake broker's `Reset` and `SetPosition` account scripts (#753).
