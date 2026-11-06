- EDGAR limiter waits at most one interval when its stamp is ahead of the clock; the CI fetch-pool hang (#3039) is gone
### Fixed
- EDGAR rate limiter: a stamp ahead of the clock (a stepped-back or faked clock) now waits one interval instead of sleeping the whole gap under the lock, which hung the filename fetch pool's threads (#3039)
