- #789 alpaca_raw batches bars and corporate-actions symbols (alpaca.symbols_per_request, default 1000); unbatched 9,500-symbol GET got HTTP 414
### Fixed
- Alpaca bars and corporate-actions requests are split into batches of at most `alpaca.symbols_per_request` symbols (default 1000) and merged in order, failing closed on any batch; a single 9,500-symbol request returned HTTP 414 (#789).
