- #3188 (PR #3225): `paper start` refuses `release_open` while a data release is open (read chunk and under the write); paper spec req 14 amendment 2026-11-07.
### Fixed
- `paper start` now refuses (`release_open`) while a data release is open, so no window starts on a store a repair is still changing (#3188).
