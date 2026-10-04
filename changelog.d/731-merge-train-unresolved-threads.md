- #731: merge_train eligibility check (j) refuses a PR with an unresolved review thread (GraphQL reviewThreads, every page) at build time; spec req 1 amended; ruleset keeps required_review_thread_resolution.
### Fixed
- merge_train build now lists a PR with an unresolved review thread INELIGIBLE under req 1 (j) instead of letting GitHub refuse it mid-merge (#731)
