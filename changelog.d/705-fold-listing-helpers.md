- #705 fold: reconcile_run and the window flatness check read the shared listing tie rule (now in execution/plan.py, re-exported as planning.current_listings); an undated listing no longer raises TypeError there.
### Fixed
- reconcile_run explanations and the paper-start flatness check use the shared current-listing rule, so a listing with no valid_from no longer raises TypeError (#705).
