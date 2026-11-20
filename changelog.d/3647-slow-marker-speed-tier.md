- #3647: a registered slow marker, strict markers, and heavy shard units auto-marked slow; uv run pytest -m 'not slow' is the fast tier.
### Added
- tests: a `slow` marker set at collection from the shard weight table (`_SLOW_UNIT_WEIGHT_S`), so `uv run pytest -m "not slow"` runs the fast tier; `--strict-markers` makes a mistyped marker fail collection (#3647).
