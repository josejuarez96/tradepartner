### Fixed
- CI: runs on `main` are no longer cancelled by the next merge (a group per commit on main; `cancel-in-progress` only on other refs), so every merge gets a completed run (#198).
