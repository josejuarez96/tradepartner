- health non_overlapping_listings accepts owner-listed same-day pairs (#855): universe.accepted_same_day_pairs (default empty); health lists every accepted pair; all other same-start pairs still fail.
### Added
- `universe.accepted_same_day_pairs` (#855): the owner can accept a reviewed same-start listing pair of different tickers (`"<security_id>@<valid_from>"`, e.g. HACAR/HCACR) so `health --check` passes it; `health` lists every accepted pair, and every other same-start pair still fails (spec req 11).
