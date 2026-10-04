- ingest/backfill: a FAILED run row's message now names where the error was raised (compact, redacted frame trail; #573, PR #577)
### Added
- ingest: a failed run row's message appends a compact, redacted `| at: file:line in function < ...` frame trail (innermost ~8 frames, across the `raise ... from` chain); bounded by new `ingest.max_where_frames`/`max_where_chars` config (#573)
