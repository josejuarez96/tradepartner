- Forced-exit assets-read halt pinned, reused-ticker exit and non-numeric cost drift now fail closed (#568, #580, PR #639).
### Fixed
- Forced exits no longer resolve a delisted name's ticker against a new issuer who reused it; `paper start` refuses a non-numeric registered cost value (`costs_drift`) instead of raising a plain `ValueError`/`TypeError` (#568, #580).
