- #553: the unspent_cash alert also fires when a later run's step 3 writes executed (a late fill), on the broker's cash and equity read at that moment.
### Fixed
- `paper run`: a rebalance that reaches `executed` on a later run (a fill collected late by step 3) now gets the `unspent_cash` test, on the broker's cash and equity read just after `executed` is written (#553).
