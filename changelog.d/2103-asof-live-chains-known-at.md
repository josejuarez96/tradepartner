- #2103 `asof.live_chains` gates the superseding row on `known_at <= t` (PR #2111)
### Changed
- Point-in-time: `asof.live_chains` requires the superseding `successor_chains` row to be known at `t`, so a retraction or revision written after `t` no longer hides a chain that was known at `t` (#2103).
