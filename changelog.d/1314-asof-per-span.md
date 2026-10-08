- #1314 item 1: Alpaca price ingest asks a security whose ticker span is not the ticker's latest open span with asof inside its own span, with a no-asof fallback when Alpaca names no company; full re-ingest makes ~45k extra requests.
### Fixed
- Alpaca price ingest no longer files a later company's bars under an old company whose ticker was reused (#1314): spans that are not the ticker's latest open span are asked with `asof` inside their own span, with a no-`asof` fallback only when Alpaca names no company at that `asof`.
