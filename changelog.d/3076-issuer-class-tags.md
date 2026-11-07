- #3076 (#3053 item 6) PR 1: classifications.issuer_class tags spac/bdc/mlp/foreign (schema 48); a domestic class is common only after a header SIC or periodic report; rebuild classifications at the sitting
### Added
- `classifications.issuer_class` (schema 48): spac, bdc, mlp or foreign, from point-in-time filing evidence (#3076)
### Fixed
- A blank check whose first S-1 names no SIC is no longer `common` before SIC 6770 shows: a domestic class waits for a header SIC or a periodic report (#3076)
