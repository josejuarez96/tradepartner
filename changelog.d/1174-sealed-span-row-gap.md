- store.research.register_dataset: a sealed split is checked against its rows, not just its two span endpoints, so a row in a gap between sealed periods is refused (#1174).
### Fixed
- store.research.register_dataset: check a sealed split's per-row event dates where the sealed periods do not cover its whole span, so a row between two periods is refused (#1174)
