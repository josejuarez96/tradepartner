- Late delisting cash (#411, PR pending): a delisted holding removed with no cash accepts a symbol-tagged broker credit for N sessions as proceeds; overdue is recorded, never halts.
### Fixed
- A delisted holding the broker removes before paying no longer halts the book on cash: a credit tagged to its symbol within risk.reconcile_merger_lag_sessions sessions books as its proceeds (#411).
