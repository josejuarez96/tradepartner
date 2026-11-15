- Paper resume now checks broker order shape and records accepted read-back time and JSON; a client order id mismatch remains fail closed (#3378).
### Fixed
- Paper resume checks acknowledged order shape and records the broker read-back timestamp and JSON; a regression test pins confirmed-order client id mismatch refusal (#3378).
