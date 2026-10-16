- #1720 `health --check` after the 2026-10-15 ingest: a FINRA "D" OTC pair and two dividend groups
### Fixed
- The OTC pair rule (`non_overlapping_listings`) treats a same-start off-exchange pair where one ticker is the other plus FINRA's fifth-letter `D` temporary symbol (`JFIL`/`JFILD`) as one line, with a failing-first test (#1720)
- The reviewed dividend list covers the two equal-amount groups the 2026-10-15 ingest added (PNNT 2026-10-15 `0.04` distinct; PFLT 2026-10-15 `0.0033` duplicate), so `duplicate_dividends` passes (#1720)
