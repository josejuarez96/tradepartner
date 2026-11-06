- #2980: health duplicate_dividends fails a reviewed retract id re-sent under another key (noisy amount or ex-date) beside another live payment
### Fixed
- health `duplicate_dividends` now fails when a reviewed duplicate's retract id comes back with a noisy amount or another ex-date beside the kept payment, instead of hiding it as repaired (#2980)
