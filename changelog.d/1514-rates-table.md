- T169 (#1514): schema 23 adds the empty `rates` table (`release` provenance), `RateObservation`, `rate_known_at`, the fixture rate source and a generated DTB3-shaped fixture series with the six spec cases; nothing writes or reads it yet.
### Added
- Schema version 23: the point-in-time `rates` table (`release` provenance), the `RateObservation` record, the `rate_known_at` stamp rule (close of the first XNYS session after the vintage date), the fixture `RateSource` and a generated DTB3-shaped fixture series (T169, #1498).
