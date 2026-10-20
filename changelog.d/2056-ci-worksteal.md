- CI shards use work stealing and run their heaviest test units first.
### Changed
- CI pytest shards use worksteal, order units by measured weight, and skip the redundant collection pass.
