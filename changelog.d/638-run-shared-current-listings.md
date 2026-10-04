- run.py uses planning.current_listings, the shared listing tie rule, instead of its own copy ([#638](https://github.com/josejuarez96/tradepartner/issues/638)); the frozen-costs sha check stays open.
### Changed
- `execution/run.py` reads current listings through `planning.current_listings` (one tie rule for planning, the wrapper and the run) instead of a private copy (#638).
