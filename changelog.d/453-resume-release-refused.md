- #453 main green: `switch.release` raises a typed `ReleaseRefused` (a `ValueError`) on every refusal; `paper resume` maps it to a refused outcome with the switch still engaged
### Fixed
- Execution: `paper resume` reports a refused release (`switch.ReleaseRefused`) as a refusal instead of raising, after #429 and #444 met on main (#453)
