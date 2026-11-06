- #2996: a bare paper reconcile after a stop run's sells fill, before collection, defers (fills_lagging) instead of halting on cash; each lagging order counts as a fill in the cash bound
### Fixed
- paper reconcile: a bare reconcile after a pre-open stop run's sells fill, before any collector journals them, no longer halts the book on cash: each lagging order counts as one fill in the cash bound, and the command names `paper resume` / the next `paper run` as the collector (#2996)
