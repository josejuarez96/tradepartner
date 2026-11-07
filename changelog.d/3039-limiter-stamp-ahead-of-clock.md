- EDGAR filename fetch pool can no longer hang: a job picked up after its turn keeps its own future, and the limiter waits at most one interval (#3039)
### Fixed
- EDGAR filename fetch pool: a job a worker picked up only after the caller had taken its future out of the shared dict died unseen, and the caller waited on that future forever; each job now carries its own future, context and record (#3039)
- EDGAR rate limiter: a stamp ahead of the clock (a stepped-back or faked clock) now waits one interval instead of sleeping the whole gap under the lock (#3039)
