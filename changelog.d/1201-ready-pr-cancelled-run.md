- ready_pr re-triggers a cancelled ci:full run once (remove and re-add the label) before reporting CI cancelled; never green without a completed successful checks on the head (#1201).
### Fixed
- ready_pr no longer stalls a draft when a later draft run cancels the ci:full run: it re-triggers once, then fails as CI cancelled (#1201)
