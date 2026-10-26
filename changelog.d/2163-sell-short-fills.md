- Alpaca adapter reads a short sale's `sell_short` fill activity as a sell instead of faulting every `fills()` read ([#2163](https://github.com/josejuarez96/tradepartner/issues/2163))
### Fixed
- The Alpaca adapter's `fills()` accepts the `sell_short` activity side (#2163); a cover is a plain `buy`, and any other side string still faults.
