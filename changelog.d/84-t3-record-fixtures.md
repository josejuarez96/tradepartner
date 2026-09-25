### Added
- Phase 2 T3: recorded Alpaca and EDGAR fixtures (scrubbed, trimmed and gzipped to stay under 500 KB), `AlpacaConfig.historical_feed` (default `sip`, confirmed on the free plan), `alpaca_raw.daily_bars` takes its default feed from config, free-data terms research report (#84).
### Changed
- `universe.liquidity_rule_enabled` now defaults to `true`: SIP history makes median dollar volume a real liquidity measure (#84).
