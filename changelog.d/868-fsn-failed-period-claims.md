- #868: after an FSN period fails whole, later periods in the pass are only parsed (failures still listed) and write no manifest or cache, so none claims a shared accession.
### Fixed
- EDGAR FSN: after a period fails whole, the later periods of the same pass no longer write manifests or per-CIK caches, so a later period cannot claim an accession the failed period shares and keep it once that period is repaired (#868).
