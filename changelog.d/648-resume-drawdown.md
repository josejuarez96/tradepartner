- #648 item 1: paper resume checks the window's marks for a drawdown crossing before releasing, so a crashed run's marks are not dropped from the check; run._drawdown's pure selection logic moved to execution/drawdown.py, shared by both.
### Fixed
- paper resume checks the drawdown on the window's marks before releasing (owner decision 2026-10-03), closing the crashed-run gap in run._drawdown's docstring; the shared pure check now lives in execution/drawdown.py.
