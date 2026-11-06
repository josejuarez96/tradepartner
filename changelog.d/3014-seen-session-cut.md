- #3014: the reconcile seen set counts a booking or rebook only from its own session on, the ledger's cut
### Fixed
- A reconcile stated through a session before a later owner true-up reads the booked activity as unseen, matching the ledger through that session, instead of halting on a cash mismatch (#3014)
