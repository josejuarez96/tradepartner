- EDGAR cover parser fails closed on a nil iXBRL fact with text; the scheduling runbook says how to remove a superseded EDGAR cache tree after a version bump ([#615](https://github.com/josejuarez96/tradepartner/issues/615)).
### Added
- Runbook: "After an EDGAR cache version bump" (scheduling.md) lists the versioned cache trees and the manual removal of superseded ones; nothing deletes them (#615).
### Fixed
- EDGAR: `parse_cover_page` raises on a cover fact marked `xsi:nil="true"` that also has text, instead of skipping it (#615); no `COVER_VERSION` bump.
