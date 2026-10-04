- #730: #723 follow-ups: end-to-end tests that a fill tied with the latest reconciliation rolls its collection back and halts the run; the halt read's failure note keeps its scrubbed message; append docstring reflowed.
### Fixed
- The halt path's own read now keeps the (scrubbed) error message in its failure note, not only the exception type; end-to-end tests pin that a fill tied with the latest reconciliation rolls back its collection and halts the run (#730).
