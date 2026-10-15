- #1673 items 1-2: Alpaca spin-offs kept when only the parent is requested (neither counted on the report); a second distribution in one payload is a revision, not a malformed category
### Fixed
- Alpaca spin-offs: a partial request asking only for the parent keeps the spin-off, and two distributions from one parent to one child in a payload are both returned as revisions of one record (the writer stores the one known at its run) instead of skipping the category; differing rows on one process date leave only that pair out, named (#1673)
