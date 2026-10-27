- Small-account whole-share buys now record the rounded order amount and close permanently sub-minimum attempts with a skip row (#2308).
### Added
- Small-account buys that become fractionable match their planned notional to the minimum order; whole-share buys worth less than the minimum record skip_below_minimum instead of staying deferred (#2308).
