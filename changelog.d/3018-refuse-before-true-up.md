- #3018: a reconcile stated before a later true-up refuses before any broker read; resume reports it as a refusal
### Fixed
- A reconcile stated through a session before a later true-up (owner, reversal or auto) now refuses before any broker read or write, instead of writing break events after `trued_up` and halting the next session on cash; `paper resume` reports it as a refusal (#3018)
