- T184b (#1650): EDGAR fetches and per-CIK cache for filing-name listings (switch off by default; capped streamed 10-K read; fetch-pass counts); the store ignores them until T184e
### Added
- EDGAR source reads a 10-K's instance prefix and Section 12(b) table, the first cover filing's prefix and a Form 25's exhibit ticker into a per-CIK extract cache (off by default; no master rows yet)
