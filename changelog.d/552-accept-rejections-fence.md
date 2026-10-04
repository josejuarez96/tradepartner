- Fixed #552: the accept_rejections fence refuses def/async def/class bindings and starred-display positional passes; require_journal's error names the missing journal without pinning a schema version.
### Fixed
- accept_rejections fence refuses def/class bindings and `*[x]`/`*(x,)` positional passes (#552); JournalNotInitialised message no longer pins schema version 4.
