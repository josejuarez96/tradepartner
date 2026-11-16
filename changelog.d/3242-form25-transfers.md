task: T387 (PR #3250)
- A Form 25 for a security that keeps trading is read as a transfer to its next listing; the 16 sitting CIKs pass both health rules but NHIC (#3242, PR #3250).
### Fixed
- store: a Form 25 followed by trading is a transfer (known_at unchanged); repair-resolution keeps those bars; non_overlapping_listings and no_bars_after_delisting follow (#3242).
