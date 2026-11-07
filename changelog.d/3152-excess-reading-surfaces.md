- Reconciliation names an excess order reading (journaled fills above the broker's filled_quantity) as a bad_reading mismatch, so the book halts and paper status shows it instead of the decision sitting in flight (#3152).
### Fixed
- paper: an excess order reading is a `bad_reading` reconciliation mismatch naming the order, instead of being reported to nothing (#3152).
