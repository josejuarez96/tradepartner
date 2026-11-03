- T365 follow-ups: nightly index step ignores the backfill row's finish time; backfill refuses a non-backfill EDGAR adapter
### Fixed
- The nightly index step no longer passes a statement backfill's finish time to quarter selection; the backfill session refuses an EDGAR adapter outside statement-backfill mode; the per-name refusal says to revert before the nightly (#2873)
