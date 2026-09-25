### Changed
- `tradepartner.calendar` caches the configured range per config state (`CALENDAR*` environment variables and the `.env` file's path, mtime and size) instead of calling `get_settings()` on every helper call; a change to either still takes effect (#154).
