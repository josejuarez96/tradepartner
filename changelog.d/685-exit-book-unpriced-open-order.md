- Step 7's read prices held names strictly; an unrelated stale order's name with no bar no longer stops the run ([#685](https://github.com/josejuarez96/tradepartner/issues/685), #569 option (b)).
### Fixed
- `paper run` step 7 (`run._exit_book`) no longer stops every run on one stale open order whose name has no bar at close(S-1): held names are priced strictly, other names only where a number reads them, and the open sells it hands to the exits are the held names' (#685).
