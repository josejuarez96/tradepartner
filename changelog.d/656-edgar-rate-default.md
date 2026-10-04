- edgar.requests_per_second default lowered 10 -> 9 (#656)
### Changed
- EDGAR request rate now defaults to 9 per second instead of SEC's 10 ceiling, after reports of 429s at 9.7 (#656).
