- T193 (#1695): split-context cover pages join inside one class only; FSN's real `EquityComponents` axis read (NiSource's common was dropped); switch stays off until T198. Census on #1695: 315 key member-months reachable.
### Fixed
- EDGAR cover pages under `edgar.cover_split_contexts` (off until T198): `parse_fsn` reads FSN's real `EquityComponents` axis spelling, so NiSource's common is no longer dropped beside its `NIMC` units; the join never carries a symbol or exchange onto a unit, note, warrant or right title (#1695, T193).
