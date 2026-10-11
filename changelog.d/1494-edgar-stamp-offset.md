- #1494: new per-CIK EDGAR stamps are checked against the SGML header before caching (SEC's submissions API served them 4 h late from ~2026-10-01); up to ~17.9k late stamps already written await the owner's #1382 call.
### Fixed
- EDGAR stamping checks each CIK's new per-CIK stamps against one SGML header and takes every stamp from its own header when SEC's submissions API disagrees (it served acceptance times late by New York's UTC offset from 2026-10) (#1494).
