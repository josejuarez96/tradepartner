- #3074: `refetch-actions --span` asks named securities once over the whole range, with every process date up to today. The sitting's step 8 tells whether Alpaca lacks SPY 2016-03-18 / 2018-06-15 or processed them late.
### Added
- `tradepartner refetch-actions --span --security ...`: one request over the whole range for the named securities, with the process-date window reaching today (`backfill.span_settings`). The dry run lists every row it would write (#3074).
