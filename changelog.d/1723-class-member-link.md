- T190d #1723: the per-class shares link at ingest behind edgar.class_member_link (off; T198 flips it): 438 new facts rows store-wide, 0 moved, 0 lost; FOX's 12 months a named residual.
### Added
- `edgar.class_member_link` (default off): shares facts link to their class at ingest by the member's words, a nominal-class rule for lone lettered members, and a dead-class rule for totals (#1723, T190d).
