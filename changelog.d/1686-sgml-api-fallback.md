- EDGAR stamp check falls back to the API stamp after N unread runs (#1686), counted in health
### Added
- edgar.stamp_sgml_fallback_runs: a CIK whose three newest SGML anchors stay unreadable for N runs gets its API stamps (late by the NY offset, never early); counted on the EDGAR run message shown by health (#1686)
