- #462 main green again: the T60 halt-read skew test pins the halt path's utc_now() instead of racing the wall clock past the fixture's CLOCK_START
### Fixed
- test_wrapper_core's halt-read skew test no longer depends on the real date: utc_now() pinned (#462)
