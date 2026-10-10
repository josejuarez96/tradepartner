- #1414 fixed: the sweep path (lab) no longer keeps each step's marking frame, so a daily sweep's memory stays flat instead of growing ~77 MB per step; results unchanged (framefix).
### Fixed
- Sweeps no longer run out of memory: `engine.run_many(..., keep_marking_frames=False)` from the lab drops the per-step marking frames only the `bt` oracle reads (#1414).
