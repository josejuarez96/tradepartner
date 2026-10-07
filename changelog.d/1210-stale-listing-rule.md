- #1210 (#1199 step 2): the survivorship gap's stale-listing rule: a live listing dark > gap.stale_listing_sessions (63, frozen; H1 reads 63) before W leaves L and M, reported as stale_listings on the gap and the health page.
### Added
- Survivorship gap: the stale-listing rule (ADR 0003 amendment #1199), `gap.stale_listing_sessions = 63`, its `FROZEN_KEY_DEFAULTS` entry and the `stale_listings` side category on `SurvivorshipGap`, the health page and `health`.
