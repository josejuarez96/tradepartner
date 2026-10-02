- #576: a per-CIK companyfacts API 200 {} is no facts, like a 404 (facts_api_empty); a submissions API 200 {} lists nothing and leaves rows unstamped, never cached unstampable (submissions_api_empty)
### Added
- EDGAR: a per-CIK companyfacts API answer of 200 {} is treated like a 404 (no XBRL facts, cached) and counted as empty API facts; a submissions API 200 {} leaves its rows unstamped for the run instead of failing the source (#576)
