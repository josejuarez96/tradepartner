- #784: staleness counts only benchmarks and common names on universe.exchanges; snapshot_static-only names with no bars and names already dark the chunk before are reported, not counted. 2016-01: 570/2,335 to 0/1,756.
### Fixed
- Staleness (backfill month and daily check) no longer counts OTC names; it reports by cause, not counts, snapshot_static-only names with no bars and names with no bar in the previous chunk, so a dark name counts once (#784).
