- T248c: schema v35 adds the point-in-time mergers table, mergers_as_of, the fixture's cash and stock mergers; trial vintage leaves it out (#1762)
### Added
- Schema version 35: the `mergers` table (acquiree, acquirer, share ratio, cash per share, three dates; stamped at the close after process_date), `mergers_as_of`, and the fixture's cash and stock mergers; `data_vintage` leaves the table out (#1762)
