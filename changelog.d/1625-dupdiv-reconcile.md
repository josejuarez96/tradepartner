- #1625: paper reconciliation counts a listed pending duplicate dividend once and halts on an unexplained equal-amount group on a held name (`unexplained_dividend_group`) instead of summing it.
### Fixed
- Paper reconciliation no longer doubles the cash due for a dividend Alpaca stores twice: a listed duplicate counts once, and an equal-amount group the reviewed list does not explain is an `unexplained_dividend_group` mismatch (#1625).
