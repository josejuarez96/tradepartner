- Merge train and ready_pr judge each named check by its latest run on the PR head (#2394).
### Fixed
- A stale failed check run on a PR head no longer blocks ready_pr or merge train after a later run of the same name succeeds; latest failures and pending runs still block (#2394).
