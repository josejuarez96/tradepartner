### Added
- `prices.announcement_date_known_at(day)`: the close of the first XNYS session after a date-only announcement (#83).
### Changed
- Schema version 2: `corporate_actions` gains nullable `announced_at TIMESTAMPTZ`; `CorporateAction.announced_at`; the fixture price adapter checks first-seen `known_at == min(announced_at, proxy)` if set, else the proxy exactly, refuses an earlier unannounced stamp and a revision that changes `announced_at`; `insert_row` binds `None` as NULL (NOT NULL columns still refuse it); fixture CSV and generator carry the column (#83).
