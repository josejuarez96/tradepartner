- Recurring-pair entries (dividend_recurring_pairs.csv) accept a security's equal-amount base plus supplemental dividends from a start date, so PNNT stops adding a duplicate_dividends violation each month (#3462).
### Fixed
- duplicate_dividends: a per-security recurring pair entry (PNNT, group size 2) accepts equal-amount same-ex-date groups of exactly that size from its start; other sizes or differing amounts still fail (#3462).
