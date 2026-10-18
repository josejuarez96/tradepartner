- T192b built: --fill-holes also lists a month whose session the resolver assigns has no traded bar between two stored traded bars (mid-month, first-day-in-lead, halts unfillable); heads and tails not listed; class B.
### Fixed
- `--fill-holes` now lists mid-month and first-day gaps between a security's traded bars (#1721), not only rename-lead gaps; halts stay listed as holes the source cannot fill
