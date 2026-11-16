- #3230: a cover page's dei:SecurityExchangeName written as a registered venue's full name ("Cboe BZX Exchange, Inc.") is read by the exact-name table instead of failing the filing on edgartools' exchnameen transform.
### Fixed
- EDGAR cover pages: `ixt-sec:exchnameen` exchange names the exact-name table holds ("Cboe BZX Exchange, Inc.", "Nasdaq PHLX LLC", "NYSE Chicago, Inc.") read as their code instead of failing the filing or slugging (#3230).
