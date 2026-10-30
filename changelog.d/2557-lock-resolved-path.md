- Paper run lock paths built from the resolved store path (#2557): a symlink and its target share one lock
### Fixed
- The paper run lock (per-book and shared .paper.lock) is taken on the fully resolved store path, so a symlink and its target can no longer both hold the same book (#2557).
