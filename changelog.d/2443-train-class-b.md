- A merge train refuses a class-B PR unless it carries the owner-ok label (#2443).
### Fixed
- `merge_train.py build` could batch a class-B PR without the owner's word; check (k) now requires the `owner-ok` label (#2443).
