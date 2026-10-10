- #1400: a RiskGatedBroker built for one book refuses another book's run with BookMismatchError, and its halt path then touches neither book's switch, orders or alerts; H1 (no book_id) unchanged.
### Fixed
- A wrapper built for one book no longer engages another book's kill switch, cancels its orders or files its alert when handed that book's run; it raises BookMismatchError instead (#1400).
