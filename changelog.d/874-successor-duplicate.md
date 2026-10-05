- #874 resolver: a repair successor's row duplicating its predecessor's no longer makes the ticker ambiguous; MTCH, FYBR, DEN, CHRD resolve to their successor ids (refill needed).
### Fixed
- Price resolver: a repair successor (`<cik>@<date>`) holds a ticker its predecessor's duplicate row shares, instead of the ticker resolving to nobody (#874).
