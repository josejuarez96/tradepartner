- T164e (#1455): ingest writes filing_events behind edgar.filing_events_enabled (default false), with rebuild, run counts, health counts and four rules; the real-store run is the owner's step; CLI flag and clock wiring are #1458.
### Added
- `filing_events` ingest behind `edgar.filing_events_enabled` (default off) and `edgar.event_forms`; `health` reports per-form counts, the last run's counts with two warnings, the clock detector rule, and four integrity rules (#1358, T164e).
