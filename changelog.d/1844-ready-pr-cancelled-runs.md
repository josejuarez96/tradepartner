- ready_pr ignores a CANCELLED check superseded by a later successful run on the head (#1844)
### Fixed
- ready_pr no longer reports CI failure for a cancelled draft-run check whose name differs from the full run's when a later run on the same head succeeded (#1844)
