- Test: a failing in-window run of book A leaves book B's orders and account separation intact; B's sizing is not yet isolated (#2475, strict xfail).
### Added
- Two-book isolation test for a failing in-window run (#2459); it found #2475 (open_buy_reserve is store-wide), pinned as a strict xfail.
