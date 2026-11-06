- #2961: a reconcile stated before a true-up reversal's session refuses before any write; the seen set cuts reversals at the ledger's session (PR #3010)
### Fixed
- A reconcile stated through a session before a true-up reversal refuses before any broker read or write instead of halting on a book-level cash mismatch; the seen set un-sees a reversed activity only from the reversal's session on (#2961)
