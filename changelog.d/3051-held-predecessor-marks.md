- #3051: a held chained predecessor is marked and priced at its successor's bars under the chain known at each read's cut; a forked chain or an uncountable successor split halts with an alert, never a crash.
### Fixed
- Paper runs: a held chained predecessor (#821) no longer crashes marks or reference prices once its chain is known; it reads its one live successor's bars point-in-time, and the run's forced exit of it is no longer refused as a reused ticker by its own successor (#3051).
