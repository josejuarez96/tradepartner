- paper resume verifies frozen_json against frozen_sha256 before any read or write (#2017)
### Fixed
- `paper resume` refuses (LimitBreachError) when the window's frozen_json no longer hashes to its frozen_sha256, as a run does, before any broker call or row (#2017)
