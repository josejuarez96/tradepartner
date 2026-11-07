- #3036: data-foundation sec_forms case (a) and event-data Backfill now state the #2865 unstampable rule (a fetch edgar.unstampable_retry_days past the settle).
### Changed
- Specs: the unstampable rule waits `edgar.unstampable_retry_days` past the quarter's settle before an accession is cached unstampable (#3036, follows #2865).
