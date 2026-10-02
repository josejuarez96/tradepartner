- Paper wrapper: a buy the last buys phase submits that expires unfilled or is rejected is now written off as soon as collection returns (#538).
### Fixed
- Paper wrapper's buys phase writes off a terminal buy (expired or rejected) during collection, not only at the end of the phase, matching the spec's "Two phases" criterion (#538).
