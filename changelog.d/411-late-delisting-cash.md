- Late delisting cash (#411): a delisted holding removed with no cash accepts a symbol-tagged broker credit for N sessions as proceeds; overdue shows in paper status and Operations, never halts.
### Fixed
- A delisted holding the broker removes before paying no longer halts the book on cash: a tagged credit within risk.reconcile_merger_lag_sessions sessions (capped by risk.delisting_cash_max_premium) books as proceeds; overdue shows in paper status (#411).
