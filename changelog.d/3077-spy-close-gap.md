- SPY close vs official close (#3053 item 11): vendor definition, not ingest; close-gap check added
### Added
- tradepartner close-gap: flags stored benchmark closes more than index_check.max_close_gap (0.5%) from official closes (SPY Alpaca SIP close differs up to 2.9% in March 2020; open/high/low match; vendor close definition, not feed/adjustment)
