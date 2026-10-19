- ready_pr marks a review stale only when the PR's own src/tests/scripts patch changed, not on a main merge or docs/plan change (#1851)
### Fixed
- ready_pr review staleness compares the PR's own patch at the reviewed sha and head; main merges and docs/changelog.d changes no longer stale a review (#1851)
