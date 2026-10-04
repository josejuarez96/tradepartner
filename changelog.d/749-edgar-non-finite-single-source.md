- #749: EDGAR facts withhold a NaN or infinite value even when only one source supplies the key, recorded like a collision (T11h), never served.
### Fixed
- EDGAR facts: a NaN or infinite share count from a single source (company facts, FSN or a per-document parse) is withheld and recorded like a collision instead of being served (#749).
