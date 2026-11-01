- Study failures survive a second Ctrl-C during failed-run close (#2626).
### Fixed
- Guard failed study close against a second SIGINT, restore the prior handler, and cover the close with a regression test.
