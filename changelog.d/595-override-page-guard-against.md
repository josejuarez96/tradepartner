- #595: override page refuses a same-content resubmit (guards a fast double-click's stale second message) and scopes the outcome display to the Override page
### Fixed
- Override page: `on_submit` refuses a submit whose fields exactly match the override just written this session, closing the fast-double-click gap the emptied-reason guard alone missed; `show_outcome` now only renders on the Override page (#595)
