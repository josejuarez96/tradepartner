### Fixed
- sweep register now applies hypothesis.long_short_refusal to every variant's frozen set before any write, so a long/short sweep at 0 bp borrow is refused by name (#2051 item 1).
