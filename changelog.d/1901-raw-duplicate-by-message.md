- alpaca_trading_raw retry treats 422 40010001 as a duplicate only on the 'client_order_id must be unique' message (#1901)
### Fixed
- A retried submit refused 422 40010001 for a bad intent, zero qty or two trail fields is raised, not returned as the landed order (#1901)
