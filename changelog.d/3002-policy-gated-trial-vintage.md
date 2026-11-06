task: T328d (PR #3017)
- T328d: trial vintage counts delisting_reasons and mergers by its cutoff only under a non-none frozen delisting policy (#3017); none trials byte-identical. Family-rule gap for policy rows: #3016.
### Added
- Trial data vintage gated on the frozen delisting policy: a backfilled reason or merger row known by the cutoff stales a non-none trial, trips the lab store-change guard and makes is_current false (#3002).
