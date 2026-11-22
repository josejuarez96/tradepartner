- #3622: the Alpaca bars, corporate-actions and assets clients time out each request after alpaca.data_request_timeout_seconds (30 s) instead of hanging the ingest on a stalled socket.
### Fixed
- Alpaca market-data requests (bars, corporate actions, assets) now carry a timeout, `alpaca.data_request_timeout_seconds` (default 30 s); alpaca-py sent none, so a stalled socket hung the nightly ingest with no run row (#3622).
