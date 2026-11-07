task: T382 (PR #3164)
- T382: an operations file may differ from its parent (a sweep variant or H1) in fill price and a higher base cost only, recording parent_fingerprint and differing_keys (#3164)
### Changed
- `hypothesis register --operations-book-of` accepts a pre-lab parent (H1) and a file differing only in `execution.fill_price` and a higher `costs.per_side_bps`; `lab status` lists a pre-lab parent's book (#3118 item 2).
