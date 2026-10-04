- merge_train build reads each PR's own changelog.d fragment at its head SHA, so valid feat/fix PRs are no longer ineligible under (h) (#764).
### Fixed
- merge_train build now fills fragment_texts from the PR's own changelog.d fragment read at the recorded head SHA; every feat/fix PR was ineligible under (h) (#764)
