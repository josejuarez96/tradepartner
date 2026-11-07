- #3169: a non-finite broker filled_avg_price is a bad_reading reconcile mismatch and never journals a terminal event (fail closed)
### Fixed
- Reconcile: a NaN or infinite broker `filled_avg_price` is now a `bad_reading` mismatch even when every share is journaled, and collect journals no terminal event for it (#3169).
