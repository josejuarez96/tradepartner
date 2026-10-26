- Lot ledger sets are per book: latest-set reads, the changed-set check and lots-reconcile use their own book's set ([#2190](https://github.com/josejuarez96/tradepartner/issues/2190)).
### Fixed
- The lot ledger's current set is scoped by book, so two books no longer overwrite each other's set; paper lots-reconcile reads the configured paper.book_id's set (#2190).
