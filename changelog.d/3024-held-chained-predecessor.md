- Reconcile maps a held chained predecessor to its successor's broker symbol (chains known at close(S-1)), so a renamed successor no longer reads as a quantity break (#3024).
### Fixed
- Reconcile: a book holding a chained predecessor (#821) whose successor trades under a new ticker reconciles under the successor's broker symbol, through the chains known at close(S-1) only; with the successor also journaled, or the broker holding both symbols, nothing is guessed and the break stands (#3024).
