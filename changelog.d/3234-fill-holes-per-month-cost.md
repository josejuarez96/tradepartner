- #3239 --fill-holes: a month costs what it writes (resolver reused while the store is unchanged; symbol-change walk sliced), one progress line per month; rehearsal clone 41 -> 6.3 s/month (#3234)
### Fixed
- `ingest --fill-holes` no longer rebuilds the store resolver for a month with no hole when nothing it reads changed, its symbol-change check no longer walks each day through the calendar, and it prints one line per month as it ends (#3234).
