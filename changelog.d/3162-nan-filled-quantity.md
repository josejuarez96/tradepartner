- #3162: a non-finite broker filled_quantity is a bad_reading reconcile mismatch and never journals a terminal event (fail closed).
### Fixed
- Reconcile: a NaN or infinite broker `filled_quantity` is now a `bad_reading` mismatch instead of passing as not lagging, and collect journals no terminal event for it (#3162).
