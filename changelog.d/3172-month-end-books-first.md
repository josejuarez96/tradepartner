task: T384 (PR #3174)
- T384: `paper run` with no `--book` runs month_end books first, then week_end, then daily (ties by token), so main's buys no longer wait behind daily's run
### Changed
- `paper run` (all books) runs `month_end` books before `week_end` and `daily` ones, ties by token (#2886)
